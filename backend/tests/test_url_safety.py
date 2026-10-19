import pytest
from backend.app.services.url_safety import canonicalize_url, UnsafeURLError

@pytest.mark.parametrize("url",["file:///etc/passwd","http://localhost/admin","http://127.0.0.1/","http://192.168.1.1/","http://user:pass@example.com/"])
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeURLError):
        canonicalize_url(url)

def test_canonicalize_public_url():
    assert canonicalize_url("HTTPS://Example.COM")=="https://example.com/"
