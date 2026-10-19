"""OpenAI-compatible LLM gateway with classified errors and strict JSON output."""
from __future__ import annotations
import asyncio, json, random, re
from typing import Any
import httpx
from backend.app.core.config import get_settings

class LLMUnavailable(RuntimeError): pass
class LLMAuthError(LLMUnavailable): pass
class LLMBillingError(LLMUnavailable): pass
class LLMRateLimitError(LLMUnavailable): pass
class LLMTransientError(LLMUnavailable): pass
class LLMInvalidModelError(LLMUnavailable): pass
class LLMNetworkError(LLMUnavailable): pass
class LLMInvalidJSONError(LLMUnavailable, ValueError): pass

_json_mode_cache: dict[tuple[str, str], bool] = {}
_SECRET_RE = re.compile(r"(?i)(bearer\s+)[^\s\"']+|sk-[A-Za-z0-9_-]{8,}|tvly-[A-Za-z0-9_-]{8,}")
def _sanitize(message: str, key: str | None = None) -> str:
    result = str(message or "")
    if key: result = result.replace(key, "[REDACTED]")
    return _SECRET_RE.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", result)[:300]

async def _post(url: str, headers: dict, payload: dict, timeout: float):
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(url, headers=headers, json=payload)

def _error_for_response(response: httpx.Response, key: str | None = None) -> LLMUnavailable:
    status = response.status_code
    try:
        data = response.json()
        msg = data.get("error", {}).get("message", "") if isinstance(data, dict) else ""
    except Exception: msg = ""
    msg = _sanitize(msg, key)
    if status in (401, 403): return LLMAuthError(f"LLM authentication/permission failed (HTTP {status}). Check the configured gateway credential and model access.")
    if status == 402: return LLMBillingError("LLM gateway reports insufficient balance (HTTP 402). Check provider billing; no retry was attempted.")
    if status == 429:
        if any(x in msg.lower() for x in ("insufficient", "balance", "quota", "billing", "credit")):
            return LLMBillingError("LLM gateway quota/balance is insufficient (HTTP 429). Check provider usage; no retry was attempted.")
        return LLMRateLimitError("LLM gateway rate limit reached (HTTP 429).")
    if status in (400, 404) and any(x in msg.lower() for x in ("model", "response_format", "json_object")):
        return LLMInvalidModelError(f"LLM model or request mode is unsupported (HTTP {status}). {_sanitize(msg)}")
    if status >= 500: return LLMTransientError(f"LLM gateway temporarily failed (HTTP {status}).")
    return LLMUnavailable(f"LLM gateway rejected the request (HTTP {status}). {_sanitize(msg)}")

def _parse_json_content(content: Any) -> dict:
    if not isinstance(content, str): raise LLMInvalidJSONError("LLM response did not contain JSON text.")
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try: result = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc: raise LLMInvalidJSONError("LLM response was not valid JSON.") from exc
    if not isinstance(result, dict): raise LLMInvalidJSONError("LLM response must be a JSON object.")
    return result

async def json_completion(system_prompt: str, user_payload: dict, *, max_tokens: int = 3000) -> dict:
    settings = get_settings()
    if not settings.openai_api_key: raise LLMUnavailable("OPENAI_API_KEY is not configured.")
    base = settings.openai_base_url.rstrip("/")
    model = settings.openai_model
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    mode = settings.llm_json_mode.lower().strip()
    if mode not in {"auto", "on", "off"}: mode = "auto"
    cache_key = (base, model)
    use_json_mode = mode == "on" or (mode == "auto" and _json_mode_cache.get(cache_key, True))
    messages = [{"role":"system","content":system_prompt + ("\nReturn a single valid JSON object and no Markdown." if not use_json_mode else "")},
                {"role":"user","content":json.dumps(user_payload, ensure_ascii=False)}]
    payload = {"model":model,"temperature":0,"max_tokens":max_tokens,"messages":messages}
    if use_json_mode: payload["response_format"] = {"type":"json_object"}
    retries = max(0, min(int(settings.llm_max_retries), 4))
    for attempt in range(retries + 1):
        try:
            response = await _post(url, headers, payload, max(1.0, settings.llm_timeout_seconds))
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt < retries:
                await asyncio.sleep(min(0.5 * (2 ** attempt) + random.uniform(0, .25), 5))
                continue
            raise LLMNetworkError(f"LLM gateway network/timeout failure ({type(exc).__name__}).") from exc
        if response.is_error:
            error = _error_for_response(response, settings.openai_api_key)
            if use_json_mode and mode == "auto" and isinstance(error, LLMInvalidModelError) and any(x in str(error).lower() for x in ("response_format", "json_object", "request mode")):
                _json_mode_cache[cache_key] = False
                payload.pop("response_format", None)
                payload["messages"][0]["content"] = system_prompt + "\nReturn a single valid JSON object and no Markdown."
                use_json_mode = False
                try:
                    response = await _post(url, headers, payload, max(1.0, settings.llm_timeout_seconds))
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    raise LLMNetworkError(f"LLM gateway network/timeout failure ({type(exc).__name__}).") from exc
                if response.is_error: raise _error_for_response(response, settings.openai_api_key)
            elif isinstance(error, (LLMTransientError, LLMRateLimitError)) and attempt < retries:
                retry_after = response.headers.get("Retry-After")
                try: delay = min(10.0, max(0.1, float(retry_after))) if retry_after else min(0.5 * (2 ** attempt) + random.uniform(0, .25), 5)
                except ValueError: delay = min(0.5 * (2 ** attempt) + random.uniform(0, .25), 5)
                await asyncio.sleep(delay); continue
            else: raise error
        if use_json_mode: _json_mode_cache[cache_key] = True
        try: data = response.json()
        except ValueError as exc: raise LLMInvalidJSONError("LLM gateway returned invalid JSON envelope.") from exc
        try: content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc: raise LLMInvalidJSONError("LLM response is missing choices[0].message.content.") from exc
        return _parse_json_content(content)
    raise LLMTransientError("LLM request exhausted retry attempts.")

async def check_llm_health(live: bool = False) -> dict:
    settings = get_settings()
    if not settings.openai_api_key: return {"status":"unavailable","configured":False,"reachable":False,"detail":"API credential is not configured."}
    if not live: return {"status":"configured","configured":True,"reachable":None,"model":settings.openai_model,"base_url":settings.openai_base_url}
    try:
        result = await json_completion("Return JSON with {\"ok\": true}.", {"health_check":True}, max_tokens=30)
        ok = result.get("ok") is True
        return {"status":"reachable" if ok else "degraded","configured":True,"reachable":ok,"model":settings.openai_model,"base_url":settings.openai_base_url}
    except LLMAuthError: status="unavailable"
    except LLMBillingError: status="degraded"
    except LLMUnavailable: status="unavailable"
    except Exception: status="degraded"
    return {"status":status,"configured":True,"reachable":False,"model":settings.openai_model,"base_url":settings.openai_base_url}
