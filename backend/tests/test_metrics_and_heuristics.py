from backend.app.services.metrics import normalize_metric, period_sort_key, is_ratio_metric
from backend.app.services.heuristic_extractor import extract_heuristic
from backend.app.services import financial_extraction as fx
import asyncio

TEXT = ("For Q2 FY25, Gross NPA stood at 2.31% on a consolidated basis compared with the prior quarter. "
        "Total deposits were 5,400 crore as at September 30, 2024. Advances grew 12% over the year. "
        "Management is optimistic about FY26 outlook and the brand. " * 1)


def test_normalize_aliases():
    assert normalize_metric("GNPA") == "Gross NPA (ambiguous)"
    assert normalize_metric("GNPA", "%") == "Gross NPA ratio"
    assert normalize_metric("GNPA", "INR crore") == "Gross NPA amount"
    assert normalize_metric("Gross Non-Performing Assets") == "Gross NPA (ambiguous)"
    assert normalize_metric("Gross Non-Performing Assets", "%") == "Gross NPA ratio"
    assert normalize_metric("Unknown Thing") == "Unknown Thing"
    assert is_ratio_metric("Net interest margin") and not is_ratio_metric("Advances")


def test_period_sorting():
    keys = [period_sort_key(p) for p in ["Q1 FY25", "FY24", "FY 2025", "Q4 FY24", "March 31, 2025", "n/a"]]
    assert keys[0] == (2025, 1) and keys[1] == (2024, 0) and keys[5] is None
    assert period_sort_key("Q4 FY24") < period_sort_key("Q1 FY25")


def test_heuristic_requires_metric_value_unit_and_period_in_one_sentence():
    items = extract_heuristic({"id": 1, "url": "https://x.example/a", "content_text": TEXT})
    names = {(i["metric_canonical"], i["raw_value"], i["unit"], i["period"]) for i in items}
    assert ("Gross NPA ratio", "2.31", "%", "Q2 FY25") in names
    assert ("Deposits", "5,400", "INR crore", "September 30, 2024") in names
    # growth rate of advances (12%) is not a level and must not be recorded; no period -> nothing for outlook
    assert not any(i["metric_canonical"] == "Advances" for i in items)
    assert all("heuristic_extraction" in i["validation_flags"] for i in items)


def test_heuristic_falls_back_when_llm_missing_and_passes_gate():
    obs, lim = asyncio.run(fx.extract_observations([{"id": 1, "url": "https://x.example/a", "content_text": TEXT * 2}]))
    assert obs and all(o["validation_status"] == "source_excerpt_verified" for o in obs)
    assert any("rule-based" in l for l in lim)
    # every excerpt is verbatim in the source
    assert all(o["excerpt"] in TEXT * 2 for o in obs)
