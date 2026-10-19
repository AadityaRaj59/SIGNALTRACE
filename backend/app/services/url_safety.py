import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

class UnsafeURLError(ValueError):
    pass

def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeURLError("Only HTTP and HTTPS URLs are allowed.")
    if not parsed.hostname:
        raise UnsafeURLError("URL must include a hostname.")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs containing embedded credentials are not allowed.")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise UnsafeURLError("Local hostnames are not allowed.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise UnsafeURLError("Private, loopback, link-local, and reserved IP addresses are not allowed.")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", parsed.query, ""))

def assert_public_host(hostname: str) -> None:
    if hostname.lower() == "localhost" or hostname.lower().endswith((".localhost", ".local")):
        raise UnsafeURLError("Local hostnames are not allowed.")
    try:
        addresses = {ipaddress.ip_address(hostname)}
    except ValueError:
        try:
            records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeURLError(f"DNS lookup failed for host: {hostname}") from exc
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    if not addresses or any(not address.is_global for address in addresses):
        raise UnsafeURLError("Hostname resolves to a private, loopback, link-local, or reserved address.")
