import asyncio
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.services import research_orchestrator as orch
from backend.app.services.source_fetcher import FetchResult
from backend.app.services.source_independence import cluster_sources, registrable_domain

DOCS = {
    "https://alpha-bank.example.co.in/q1-fy24": "Alpha Bank results. For Q1 FY24, Gross NPA stood at 3.40% on a consolidated basis. Total deposits were 4,000 crore for Q1 FY24 consolidated. " * 3,
    "https://filings.example.org/alpha-q1-fy25": "Exchange filing. For Q1 FY25, Gross NPA stood at 2.90% on a consolidated basis. Total deposits were 5,000 crore for Q1 FY25 consolidated. " * 3,
    "https://alpha-bank.example.co.in/other": "Same publisher page. Some unrelated commentary about branches and customers that is long enough to be readable text. " * 5,
}

@pytest.fixture
def fake_fetch(monkeypatch):
    async def fetch(url):
        text = DOCS[url]
        return FetchResult(url, url, title=url, publisher=url.split("/")[2], content_type="text/html", content_hash=str(hash(text)), text=text, status="fetched")
    monkeypatch.setattr(orch, "fetch_public_url", fetch)

def test_domain_and_clustering():
    assert registrable_domain("https://www.alpha.co.in/x") == "alpha.co.in"
    assert registrable_domain("https://a.b.example.com/x") == "example.com"
    cl = cluster_sources([{"id": 1, "url": "https://a.example.com/1", "content_text": "alpha " * 50},
                          {"id": 2, "url": "https://b.example.com/2", "content_text": "completely different words " * 50},
                          {"id": 3, "url": "https://other.org/3", "content_text": "alpha " * 50}])
    assert cl[1]["cluster"] == cl[2]["cluster"]        # same publisher
    assert cl[1]["cluster"] == cl[3]["cluster"]        # near-duplicate text
    cl2 = cluster_sources([{"id": 1, "url": "https://a.org/1", "content_text": "one two three four five six seven " * 20},
                           {"id": 2, "url": "https://b.net/2", "content_text": "red green blue yellow orange purple pink " * 20}])
    assert cl2[1]["cluster"] != cl2[2]["cluster"]

def test_end_to_end_without_keys_uses_rule_based_extraction(fake_fetch):
    c = TestClient(app)
    r = c.post("/api/research-runs", json={"company": "Alpha Bank", "urls": list(DOCS)})
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "completed_with_limitations"  # observations/calculations exist; no defensible signal was promoted
    rep = c.get(f"/api/research-runs/{run['id']}/report").json()
    assert rep["observations"] and rep["independent_clusters"] == 2
    gnpa = [x for x in rep["calculations"] if x["metric_name"] == "Gross NPA ratio"][0]
    assert gnpa["kind"] == "basis_points" and gnpa["result_basis_points"] == "-50.00"
    assert [p["period"] for p in gnpa["series"]] == ["Q1 FY24", "Q1 FY25"]
    assert "Insufficient evidence" in rep["discovery"]
    html_export=c.get(f"/api/research-runs/{run['id']}/export.html")
    pdf_export=c.get(f"/api/research-runs/{run['id']}/export.pdf")
    assert html_export.status_code == 200 and "Data coverage" in html_export.text and "Gross NPA" in html_export.text
    assert pdf_export.content.startswith(b"%PDF")
    stages=c.get(f"/api/research-runs/{run['id']}/stages").json()["stages"]
    assert {"relevance_filter","financial_extraction_and_validation","deterministic_calculations","report_generation_and_validation"} <= {s["stage"] for s in stages}
    assert c.get(f"/api/research-runs/{run['id']}/observations").json()["observations"] == rep["observations"]
    assert c.get(f"/api/research-runs/{run['id']}/calculations").json()["calculations"] == rep["calculations"]
    assert c.get(f"/api/research-runs/{run['id']}/audit").status_code == 200

def test_background_run_cancel_and_delete(monkeypatch):
    async def slow(url):
        await asyncio.sleep(0.3)
        return FetchResult(url, url, status="error", error="stub")
    monkeypatch.setattr(orch, "fetch_public_url", slow)
    with TestClient(app) as c:      # context manager keeps the event loop alive for the background task
        run = c.post("/api/research-runs", json={"company": "Slow Co", "urls": [f"https://s{i}.example.org/" for i in range(6)], "background": True}).json()
        assert run["status"] in {"queued", "running"}
        assert c.post(f"/api/research-runs/{run['id']}/cancel").status_code == 200
        assert c.delete(f"/api/research-runs/{run['id']}").status_code == 409   # cannot delete while active
        import time
        for _ in range(60):
            cur = c.get(f"/api/research-runs/{run['id']}").json()
            if cur["status"] not in {"queued", "running"}:
                break
            time.sleep(0.1)
        assert cur["status"] == "cancelled"
        assert c.delete(f"/api/research-runs/{run['id']}").status_code == 200
        assert c.get(f"/api/research-runs/{run['id']}").status_code == 404

def test_dashboard_stats_shape():
    d = TestClient(app).get("/api/dashboard/stats").json()
    assert {"runs", "status", "observations", "sources", "top_metrics", "runs_by_day"} <= set(d)

def test_token_auth_and_rate_limit(monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main.settings, "api_token", "s3cret")
    c = TestClient(app)
    assert c.get("/api/health").status_code == 200                      # health stays public
    assert c.get("/api/research-runs").status_code == 401
    assert c.get("/api/research-runs", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    monkeypatch.setattr(main.settings, "api_token", None)
    monkeypatch.setattr(main.settings, "rate_limit_per_minute", 2)
    main._hits.clear()
    codes = [c.post("/api/research-runs", json={"company": "RL Co", "urls":["https://example.com"]}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]

def test_mocked_hdfc_bank_acceptance_pipeline(monkeypatch):
    """Mocked HDFC-shaped fixtures only; these values are test data, not live company evidence."""
    fixtures={
        'https://hdfcbank.example.org/financial-results/q1-fy24': 'HDFC Bank quarterly financial results. For Q1 FY24, Gross NPA stood at 3.40% on a consolidated basis. Total advances were 10,000 crore for Q1 FY24 consolidated. Total deposits were 12,000 crore for Q1 FY24 consolidated. '*3,
        'https://filings.example.net/hdfc/quarterly-results-q1-fy25': 'HDFC Bank exchange filing quarterly results. For Q1 FY25, Gross NPA stood at 2.90% on a consolidated basis. Total advances were 12,000 crore for Q1 FY25 consolidated. Total deposits were 14,400 crore for Q1 FY25 consolidated. '*3,
    }
    async def fetch(url):
        text=fixtures[url]
        return FetchResult(url,url,title='HDFC Bank quarterly financial results',publisher=url.split('/')[2],content_type='text/html',content_hash=str(hash(text)),text=text,status='fetched')
    monkeypatch.setattr(orch,'fetch_public_url',fetch)
    c=TestClient(app)
    response=c.post('/api/research-runs',json={'company':'HDFC Bank (NSE: HDFCBANK)','focus':'loan growth versus asset quality','urls':list(fixtures)})
    assert response.status_code==200
    run=response.json()
    report=c.get(f"/api/research-runs/{run['id']}/report").json()
    assert report['coverage']['relevant_sources']==2
    assert report['coverage']['validated_observations']>=4
    assert report['coverage']['deterministic_calculations']>=2
    assert all(o['validation_status']=='source_excerpt_verified' for o in report['observations'])
    assert run['status'] in {'completed_with_limitations','completed','abstained'}
    assert 'mocked' not in report['discovery'].lower()  # production report content stays neutral; test fixture status is documented here.
