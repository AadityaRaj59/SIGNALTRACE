import hashlib
import asyncio
from urllib.parse import urlparse, urljoin
import httpx
from backend.app.core.config import get_settings
from backend.app.services.url_safety import canonicalize_url, assert_public_host, UnsafeURLError
from backend.app.services.document_parser import parse_document

class FetchResult:
    def __init__(self, url, canonical_url, title="", publisher="", content_type="", content_hash="", text="", status="error", error=None, publication_date=None, parse_status=None, is_scanned=None, ocr_required=None, failure_reason=None):
        self.url, self.canonical_url, self.title, self.publisher = url, canonical_url, title, publisher
        self.content_type, self.content_hash, self.text = content_type, content_hash, text
        self.status, self.error, self.publication_date = status, error, publication_date
        self.parse_status, self.is_scanned, self.ocr_required = parse_status, is_scanned, ocr_required
        self.failure_reason = failure_reason

async def fetch_public_url(url: str) -> FetchResult:
    settings = get_settings()
    try:
        current = canonicalize_url(url)
        headers = {"User-Agent": settings.user_agent, "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.8"}
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=False, headers=headers) as client:
            response = None
            for _ in range(4):
                parsed_url = urlparse(current)
                assert_public_host(parsed_url.hostname or "")
                for attempt in range(3):
                    response = await client.get(current)
                    if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                        break
                    retry_after = response.headers.get("retry-after", "")
                    try:
                        delay = min(max(float(retry_after), 0.25), 3.0) if retry_after else 0.5 * (2 ** attempt)
                    except ValueError:
                        delay = 0.5 * (2 ** attempt)
                    await asyncio.sleep(delay)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect response did not include a Location header.")
                    current = canonicalize_url(urljoin(current, location))
                    continue
                break
            else:
                raise ValueError("Redirect limit exceeded.")
            if response is None:
                raise ValueError("No response received.")
            if response.status_code >= 400:
                raise ValueError(f"Source returned HTTP {response.status_code}.")
            length = response.headers.get("content-length")
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            size_limit = settings.max_pdf_bytes if content_type == "application/pdf" else settings.max_source_bytes
            if length and int(length) > size_limit:
                raise ValueError("Source exceeds configured PDF-size limit." if content_type == "application/pdf" else "Source exceeds configured response-size limit.")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > size_limit:
                    raise ValueError("Source exceeds configured PDF-size limit." if content_type == "application/pdf" else "Source exceeds configured response-size limit.")
            if content_type not in {"text/html", "application/xhtml+xml", "text/plain", "application/pdf"}:
                raise ValueError(f"Unsupported content type: {content_type or 'unknown'}.")
            digest = hashlib.sha256(body).hexdigest()
            parsed = parse_document(bytes(body), content_type, settings.max_document_chars, max_pdf_pages=settings.max_pdf_pages)
            parse_error=parsed.get("parse_error")
            parse_status=parsed.get("parse_status") or ("parsed" if not parse_error else "parser_failure")
            failure_reason=None if not parse_error else ("no_readable_text" if "no readable" in str(parse_error).lower() or "no extractable text" in str(parse_error).lower() else "parser_failure")
            return FetchResult(url, current, title=parsed.get("title", ""), publisher=urlparse(current).hostname or "",
                content_type=content_type, content_hash=digest, text=parsed.get("text", ""),
                status="fetched" if not parse_error else "partial", error=parse_error,
                publication_date=parsed.get("publication_date"), parse_status=parse_status, is_scanned=parsed.get("is_scanned"), ocr_required=parsed.get("ocr_required"),failure_reason=failure_reason)
    except (UnsafeURLError, httpx.HTTPError, ValueError, OSError) as exc:
        try:
            canonical = canonicalize_url(url)
        except Exception:
            canonical = url
        message=str(exc)[:300]
        if isinstance(exc, UnsafeURLError): reason="unsafe_url"
        elif isinstance(exc, httpx.TimeoutException): reason="timeout"
        elif isinstance(exc, httpx.NetworkError): reason="network_failure"
        elif "HTTP 403" in message: reason="http_403"
        elif "HTTP 429" in message: reason="http_429"
        elif "HTTP 404" in message: reason="http_404"
        elif "HTTP 5" in message: reason="http_5xx"
        elif "Unsupported content type" in message: reason="unsupported_content_type"
        elif "size limit" in message: reason="size_limit"
        elif "Redirect" in message: reason="redirect_failure"
        else: reason="fetch_failure"
        return FetchResult(url, canonical, status="error", error=f"{type(exc).__name__}: {message}",failure_reason=reason)
