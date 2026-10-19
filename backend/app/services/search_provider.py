"""Tavily provider with explicit errors and bounded transient retries."""
from __future__ import annotations
import asyncio, random
import httpx
from backend.app.core.config import get_settings

class SearchError(RuntimeError): pass
class SearchAuthError(SearchError): pass
class SearchQuotaError(SearchError): pass
class SearchRateLimitError(SearchError): pass
class SearchServerError(SearchError): pass
class SearchTimeoutError(SearchError): pass
class SearchNetworkError(SearchError): pass
class SearchInvalidResponseError(SearchError): pass

async def _post(url: str, headers: dict, payload: dict, timeout: float):
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(url, headers=headers, json=payload)

def _classify(response: httpx.Response) -> SearchError:
    status = response.status_code
    try:
        body=response.json(); message=str(body.get("message") or body.get("detail") or body.get("error") or "").lower()
    except Exception: message=""
    if status in (401, 403): return SearchAuthError(f"Tavily authentication/permission failure (HTTP {status}).")
    if status == 402 or (status == 429 and any(x in message for x in ("quota","credit","billing","balance"))): return SearchQuotaError(f"Tavily account reports insufficient balance or quota (HTTP {status}).")
    if status == 429: return SearchRateLimitError("Tavily rate limit reached (HTTP 429).")
    if status >= 500: return SearchServerError(f"Tavily service error (HTTP {status}).")
    return SearchError(f"Tavily rejected the search request (HTTP {status}).")

async def search_public_sources(query: str, limit: int = 8, include_domains: list[str] | None = None) -> list[dict]:
    settings = get_settings()
    if not settings.tavily_api_key: raise SearchAuthError("TAVILY_API_KEY is not configured.")
    payload = {"api_key":settings.tavily_api_key,"query":query,"search_depth":"advanced","max_results":max(1,min(limit,10))}
    if include_domains: payload["include_domains"] = list(dict.fromkeys(include_domains))[:10]
    headers = {"Authorization":f"Bearer {settings.tavily_api_key}","Content-Type":"application/json"}
    retries = max(0, min(settings.search_max_retries, 4))
    for attempt in range(retries + 1):
        try: response = await _post("https://api.tavily.com/search", headers, payload, max(1.0, settings.search_timeout_seconds))
        except httpx.TimeoutException as exc:
            if attempt < retries:
                await asyncio.sleep(min(.5 * 2**attempt + random.uniform(0,.25), 5)); continue
            raise SearchTimeoutError("Tavily search timed out.") from exc
        except httpx.NetworkError as exc:
            if attempt < retries:
                await asyncio.sleep(min(.5 * 2**attempt + random.uniform(0,.25), 5)); continue
            raise SearchNetworkError("Tavily network request failed.") from exc
        if response.is_error:
            error = _classify(response)
            if isinstance(error, (SearchServerError, SearchRateLimitError)) and attempt < retries:
                await asyncio.sleep(min(.5 * 2**attempt + random.uniform(0,.25), 5)); continue
            raise error
        try: data = response.json()
        except ValueError as exc: raise SearchInvalidResponseError("Tavily returned invalid JSON.") from exc
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list): raise SearchInvalidResponseError("Tavily response did not contain a results array.")
        return [{"url":item["url"],"title":str(item.get("title") or "")[:500],"content":str(item.get("content") or "")[:4000],"score":item.get("score")}
                for item in results if isinstance(item,dict) and item.get("url")]
    raise SearchServerError("Tavily search exhausted retry attempts.")

async def check_search_health(live: bool = False) -> dict:
    settings = get_settings()
    if not settings.tavily_api_key: return {"status":"unavailable","configured":False,"reachable":False,"detail":"Search credential is not configured."}
    if not live: return {"status":"configured","configured":True,"reachable":None}
    try:
        await search_public_sources("site:tavily.com", limit=1)
        return {"status":"reachable","configured":True,"reachable":True}
    except SearchQuotaError: return {"status":"degraded","configured":True,"reachable":False,"detail":"Quota/balance unavailable."}
    except SearchError as exc: return {"status":"unavailable","configured":True,"reachable":False,"detail":str(exc)}
