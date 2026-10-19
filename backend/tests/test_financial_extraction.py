import asyncio
from backend.app.services import financial_extraction as extraction

SOURCE_TEXT = "The bank reported Advances of 1,200 crore for FY 2025 on a consolidated basis. " + "Additional public financial information. " * 4

def valid_observation(source_id=7, source_url="https://example.org/results", excerpt=SOURCE_TEXT[:78]):
    return {"observations":[{"metric_name":"Advances","raw_value":"1,200","normalized_value":None,
        "unit":"crore","period":"FY 2025","publication_date":"model-must-not-set-date",
        "reporting_basis":"consolidated","source_id":source_id,"source_url":source_url,
        "excerpt":excerpt,"extraction_confidence":0.9,"validation_status":"pending","validation_flags":[]}]}

def test_extraction_requires_exact_excerpt_and_source_identity(monkeypatch):
    async def fake_completion(*args, **kwargs):
        return valid_observation()
    monkeypatch.setattr(extraction, "json_completion", fake_completion)
    observations, limitations = asyncio.run(extraction.extract_observations([{"id":7,"url":"https://example.org/results","content_text":SOURCE_TEXT,"publication_date":None}]))
    assert len(observations) == 1
    assert observations[0]["validation_status"] == "source_excerpt_verified"
    assert observations[0]["publication_date"] is None

def test_extraction_rejects_wrong_source_id(monkeypatch):
    async def fake_completion(*args, **kwargs):
        return valid_observation(source_id=999)
    monkeypatch.setattr(extraction, "json_completion", fake_completion)
    observations, limitations = asyncio.run(extraction.extract_observations([{"id":7,"url":"https://example.org/results","content_text":SOURCE_TEXT}]))
    assert observations == []
    assert any("source ID or URL" in item for item in limitations)

def test_extraction_rejects_fabricated_excerpt(monkeypatch):
    async def fake_completion(*args, **kwargs):
        data = valid_observation()
        data["observations"][0]["excerpt"] = "Advances 1,200 crore FY 2025 fabricated sentence not on source"
        return data
    monkeypatch.setattr(extraction, "json_completion", fake_completion)
    observations, limitations = asyncio.run(extraction.extract_observations([{"id":7,"url":"https://example.org/results","content_text":SOURCE_TEXT}]))
    assert observations == []
    assert any("exact source substring" in item for item in limitations)


def test_decimal_normalization_for_crore_and_percent():
    assert extraction.normalize_observation_value("1,200", "crore") == "12000000000"
    assert extraction.normalize_observation_value("2.9", "%") == "2.9"
    assert extraction.normalize_observation_value("unknown", "crore") is None


def test_runtime_quota_error_uses_rule_fallback(monkeypatch):
    async def quota_error(*args, **kwargs):
        raise RuntimeError("LLM provider rate limit or quota reached.")
    monkeypatch.setattr(extraction, "json_completion", quota_error)
    text = "GNPA ratio improved to 2.9% in FY 2025. " + "Additional bank financial information. " * 5
    observations, limitations = asyncio.run(extraction.extract_observations([{
        "id": 9, "url": "https://example.org/report", "content_text": text, "publication_date": None
    }]))
    assert any("fell back" in x.lower() or "switching to rule-based" in x.lower() for x in limitations)
    # The extractor may conservatively abstain if its strict period/metric patterns don't match;
    # the critical regression is that the provider failure enters the fallback path.

def test_normalizer_does_not_assign_currency_to_bare_million():
    from backend.app.services.financial_extraction import normalize_observation_value
    assert normalize_observation_value('5','million') is None
    assert normalize_observation_value('5','USD million')=='5000000'
    assert normalize_observation_value('1,23,456.78','INR crore')=='1234567800000'
