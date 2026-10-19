from fastapi.testclient import TestClient
from backend.app.main import app

def test_health():
    r=TestClient(app).get("/api/health")
    assert r.status_code==200
    assert r.json()["status"]=="ok"

def test_run_without_urls_or_search_key_returns_needs_input(monkeypatch):
    from backend.app.core.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "tavily_api_key", None)
    c = TestClient(app)
    r = c.post("/api/research-runs", json={"company":"Test Finance Ltd","focus":"asset quality","urls":[]})
    assert r.status_code == 422
    body = r.json()["detail"]
    assert body["status"] == "needs_input"
    assert "public source URL" in body["message"]


def test_pdf_export_is_available_for_saved_run():
    client = TestClient(app)
    created = client.post("/api/research-runs", json={"company":"PDF Export Test Ltd", "urls":["https://example.com"]})
    assert created.status_code == 200
    run_id = created.json()["id"]
    response = client.get(f"/api/research-runs/{run_id}/export.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
