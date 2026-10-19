"""Canonical metric registry. Aliases are candidate matches; unit/basis validation remains mandatory."""
import re
METRIC_CATALOG: dict[str, dict] = {
    "Gross NPA ratio":{"id":"gross_npa_ratio","aliases":["gross npa ratio","gross npa","gnpa ratio","gnpa","gross non-performing assets ratio","gross non performing assets ratio","gross stage 3 ratio","gross stage iii ratio"],"ratio":True,"range":(0,100)},
    "Gross NPA amount":{"id":"gross_npa_amount","aliases":["gross npa amount","gross non-performing assets amount","gross non performing assets amount"],"ratio":False},
    "Net NPA ratio":{"id":"net_npa_ratio","aliases":["net npa ratio","net npa","nnpa ratio","nnpa","net non-performing assets ratio","net non performing assets ratio","net stage 3 ratio"],"ratio":True,"range":(0,100)},
    "Net NPA amount":{"id":"net_npa_amount","aliases":["net npa amount","net non-performing assets amount","net non performing assets amount"],"ratio":False},
    "Net interest margin":{"id":"net_interest_margin","aliases":["net interest margin","nim"],"ratio":True,"range":(-5,15)},
    "Credit cost":{"id":"credit_cost","aliases":["credit cost"],"ratio":True,"range":(-5,30)},
    "Provision coverage ratio":{"id":"provision_coverage_ratio","aliases":["provision coverage ratio","provision coverage","pcr"],"ratio":True,"range":(0,200)},
    "Capital adequacy ratio":{"id":"capital_adequacy_ratio","aliases":["capital adequacy ratio","capital adequacy","crar","capital adequacy ratio (crar)"],"ratio":True,"range":(0,100)},
    "Return on assets":{"id":"return_on_assets","aliases":["return on assets","roa"],"ratio":True,"range":(-20,30)},
    "Return on equity":{"id":"return_on_equity","aliases":["return on equity","roe"],"ratio":True,"range":(-100,200)},
    "CASA ratio":{"id":"casa_ratio","aliases":["casa ratio","current account savings account ratio"],"ratio":True,"range":(0,100)},
    "CASA deposits":{"id":"casa_deposits","aliases":["casa deposits","current account savings account deposits"],"ratio":False},
    "Advances":{"id":"total_advances","aliases":["total advances","gross advances","advances","loans and advances","total loans and advances"],"ratio":False},
    "Deposits":{"id":"total_deposits","aliases":["total deposits","deposits"],"ratio":False},
    "Borrowings":{"id":"borrowings","aliases":["borrowings","total borrowings"],"ratio":False},
    "Net interest income":{"id":"net_interest_income","aliases":["net interest income","nii"],"ratio":False},
    "Profit after tax":{"id":"profit_after_tax","aliases":["profit after tax","pat","net profit","profit for the period after tax"],"ratio":False},
    "Operating profit":{"id":"operating_profit","aliases":["operating profit","ppop","pre-provision operating profit"],"ratio":False},
    "Provisions":{"id":"provisions","aliases":["provisions","total provisions","provisions and contingencies"],"ratio":False},
    "Slippages":{"id":"slippages","aliases":["slippages","fresh slippages"],"ratio":False},
    "Slippage ratio":{"id":"slippage_ratio","aliases":["slippage ratio","slippage rate"],"ratio":True,"range":(0,100)},
    "Other income":{"id":"other_income","aliases":["other income","non-interest income"],"ratio":False},
    "Cost-to-income ratio":{"id":"cost_to_income_ratio","aliases":["cost-to-income ratio","cost to income ratio","cost income ratio"],"ratio":True,"range":(0,200)},
}
_ALIAS_INDEX=sorted(((alias,canon) for canon,d in METRIC_CATALOG.items() for alias in d["aliases"]),key=lambda x:-len(x[0]))
def alias_pattern() -> str:
    return "|".join(re.escape(a).replace(r"\ ",r"[\s-]+") for a,_ in _ALIAS_INDEX)
def canonical_metric_id(canonical_or_label: str, unit: str = "") -> str | None:
    canonical=normalize_metric(canonical_or_label,unit)
    data=METRIC_CATALOG.get(canonical)
    return data.get("id") if data else None
def normalize_metric(label: str, unit: str = "") -> str:
    clean=re.sub(r"[\s-]+"," ",str(label).lower()).strip()
    # Bare GNPA/NPA is ambiguous: resolve using unit when explicit.
    if clean in {"gross npa","gnpa","gross non performing assets","gross non-performing assets"}:
        return "Gross NPA ratio" if unit.strip().lower() in {"%","percent","per cent","bps"} else "Gross NPA amount" if unit.strip() and unit.strip().lower() not in {"unknown","unspecified"} else "Gross NPA (ambiguous)"
    if clean in {"net npa","nnpa","net non performing assets","net non-performing assets"}:
        return "Net NPA ratio" if unit.strip().lower() in {"%","percent","per cent","bps"} else "Net NPA amount" if unit.strip() and unit.strip().lower() not in {"unknown","unspecified"} else "Net NPA (ambiguous)"
    for alias,canon in _ALIAS_INDEX:
        if re.fullmatch(re.sub(r"[\s-]+"," ",alias),clean): return canon
    return str(label).strip()
def is_ratio_metric(canonical: str, unit: str = "") -> bool:
    data=METRIC_CATALOG.get(canonical)
    if data: return bool(data["ratio"])
    return unit.strip().lower() in {"%","percent","per cent","bps"}
def plausibility_range(canonical: str):
    return METRIC_CATALOG.get(canonical,{}).get("range")
def period_sort_key(period: str):
    """Return a comparable fiscal-period key, preferring explicit Indian FY parsing."""
    try:
        from backend.app.services.periods import parse_period
        parsed=parse_period(period)
        if parsed:
            if parsed.get("period_type")=="quarter": return (int(parsed["fiscal_year"]),int(parsed.get("quarter") or 0))
            if parsed.get("period_type")=="annual": return (int(parsed["fiscal_year"]),0)
            if parsed.get("period_type")=="half_year": return (int(parsed["fiscal_year"]),5 if "H2" in period.upper() else 2)
            if parsed.get("period_type")=="nine_months": return (int(parsed["fiscal_year"]),3)
            return (int(parsed.get("fiscal_year") or parsed["end"][:4]),0)
    except (ValueError, TypeError, KeyError):
        pass
    p=period.strip()
    m=re.search(r"(?:Q([1-4]))?\s*[- ]?\s*FY\s*[' ]?(20\d{2}|\d{2})\b",p,re.I)
    if m:
        year=int(m.group(2)); year=year+2000 if year<100 else year
        return (year,int(m.group(1)) if m.group(1) else 0)
    m=re.search(r"\bQ([1-4])\s*[- ]?\s*(20\d{2})\b",p,re.I)
    if m:return (int(m.group(2)),int(m.group(1)))
    m=re.search(r"(20\d{2}|19\d{2})",p)
    if m:return (int(m.group(1)),0)
    return None
