"""Deterministic, key-free extractor for explicit "<metric> ... <value> ... <period>" statements.

Used when no LLM key is configured (or as a supplement). It is deliberately conservative:
a candidate is produced only when ONE sentence contains a known metric label, a plain
number (with a unit it can see) and an explicit period label. Every candidate still goes
through the same provenance gate as LLM output (exact excerpt, value in excerpt, metric
and period in excerpt) and is flagged `heuristic_extraction` with low confidence.
"""
import re
from backend.app.services.metrics import alias_pattern, normalize_metric, is_ratio_metric

_METRIC = re.compile(rf"\b(?P<metric>{alias_pattern()})\b", re.I)
_PERIOD = re.compile(
    r"(?P<period>(?:Q[1-4]\s*[- ]?\s*FY\s*[' ]?\s*(?:20)?\d{2}|H[12]\s*FY\s*[' ]?\s*(?:20)?\d{2}|FY\s*[' ]?\s*(?:20)?\d{2}|"
    r"(?:March|June|September|December)\s+3[01],?\s+20\d{2}|Q[1-4]\s+20\d{2}))", re.I)
_VALUE = re.compile(
    r"(?P<value>[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?P<unit>%|per\s?cent|bps|crore|cr|lakh crore|lakh|billion|bn|million|mn)?", re.I)
_BASIS = re.compile(r"\b(consolidated|standalone)\b", re.I)
_UNIT_NORMAL = {"%": "%", "per cent": "%", "percent": "%", "cr": "INR crore", "crore": "INR crore",
                "lakh crore": "INR lakh crore", "lakh": "INR lakh", "bn": "billion", "billion": "billion",
                "mn": "million", "million": "million", "bps": "bps"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[])|\n{2,}")


def _clean_unit(raw: str | None) -> str | None:
    if not raw:
        return None
    return _UNIT_NORMAL.get(re.sub(r"\s+", " ", raw.lower()).strip())


def extract_heuristic(source: dict, max_items: int = 40) -> list[dict]:
    text = source.get("content_text") or ""
    out: list[dict] = []
    for sentence in _SENTENCE.split(text):
        sentence = sentence.strip()
        if not (15 <= len(sentence) <= 600):
            continue
        pm = _PERIOD.search(sentence)
        if not pm:
            continue
        for mm in _METRIC.finditer(sentence):
            # Look for the first value AFTER the metric label that is not part of the period token.
            tail = sentence[mm.end(): mm.end() + 90]
            for vm in _VALUE.finditer(tail):
                abs_start = mm.end() + vm.start()
                if pm.start() <= abs_start < pm.end():
                    continue  # digits belong to the period label
                unit = _clean_unit(vm.group("unit"))
                canon = normalize_metric(mm.group("metric"), unit or "")
                if unit is None:
                    if is_ratio_metric(canon):
                        # A bare number for a ratio metric is not safe without a visible % sign.
                        continue
                    continue
                if unit == "%" and not is_ratio_metric(canon, "%"):
                    continue  # e.g. "advances grew 12%" is a growth rate, not the level
                basis_m = _BASIS.search(sentence)
                out.append({
                    "metric_name": mm.group("metric").strip(), "metric_canonical": canon,
                    "raw_value": vm.group("value"), "normalized_value": None, "unit": unit,
                    "period": re.sub(r"\s+", " ", pm.group("period")).strip(),
                    "reporting_basis": basis_m.group(1).lower() if basis_m else "unknown",
                    "source_id": source["id"], "source_url": source["url"], "excerpt": sentence[:700],
                    "extraction_confidence": 0.45, "validation_status": "pending",
                    "validation_flags": ["heuristic_extraction"],
                })
                break
            if len(out) >= max_items:
                return out
    return out
