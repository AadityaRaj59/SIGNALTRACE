"""Evidence-gated extraction: structured table heuristics first, then LLM candidates, then rules."""
from __future__ import annotations
import html, re
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, Field, ValidationError
from backend.app.services.llm_provider import json_completion, LLMUnavailable
from backend.app.services.metrics import normalize_metric, canonical_metric_id, plausibility_range, is_ratio_metric
from backend.app.services.heuristic_extractor import extract_heuristic
from backend.app.services.units import parse_number, normalize_amount
from backend.app.services.periods import parse_period

class Observation(BaseModel):
    metric_name: str = Field(min_length=2,max_length=120)
    metric_canonical: str | None = Field(default=None,max_length=120)
    metric_id: str | None = None
    raw_value: str = Field(min_length=1,max_length=80)
    normalized_value: str | None = Field(default=None,max_length=80)
    unit: str = Field(min_length=1,max_length=80)
    normalized_unit: str | None = None
    currency: str | None = None
    conversion_rule: str | None = None
    period: str = Field(min_length=2,max_length=80)
    period_start: str | None = None
    period_end: str | None = None
    period_type: str | None = None
    publication_date: str | None = None
    reporting_basis: str = Field(min_length=2,max_length=40)
    source_id: int
    source_url: str
    excerpt: str = Field(min_length=10,max_length=700)
    page_reference: str | None = None
    table_reference: str | None = None
    table_header_excerpt: str | None = None
    extraction_method: str | None = None
    extraction_confidence: float = Field(ge=0,le=1)
    validation_status: str = "pending"
    validation_flags: list[str] = Field(default_factory=list)

class ObservationBatch(BaseModel):
    observations: list[Observation] = Field(default_factory=list,max_length=100)

SYSTEM = '''Extract only financial observations explicitly supported by the provided public source. Return JSON only: {"observations":[]}. Never infer missing values, periods, units, source IDs, URLs, reporting basis, or currency. For tables, the exact excerpt may include the relevant table header and row. Preserve metric labels as printed. Prefer advances, deposits, CASA, GNPA/NNPA ratios or amounts, NIM, NII, provisions, PAT, operating profit, credit cost, slippages, capital adequacy, ROA and ROE. Use reporting_basis=unknown unless explicitly stated. Exclude growth rates when the requested metric is a level. Confidence reflects extraction certainty, not business quality.'''

def _normalization_details(raw_value: str, unit: str, context: str = "") -> dict:
    raw=str(raw_value or "").strip().replace("−","-").replace(" "," ")
    m=re.fullmatch(r"(?P<num>\(?\s*[-+]?(?:[\d,]+(?:\.\d*)?|\.\d+)\s*\)?)\s*(?P<suffix>%|bps|bp|lakh crore|crore|cr|lakh|million|mn|billion|bn|trillion|tn)?",raw,re.I)
    if not m: return {"normalized_value":None,"normalized_unit":None,"currency":None,"conversion_rule":"raw_value_not_numeric"}
    numeric=m.group("num").strip(); suffix=(m.group("suffix") or "").strip()
    declared=unit or ""
    unit_for_conversion=suffix or declared
    meta=normalize_amount(numeric,unit_for_conversion,context)
    if not meta:
        return {"normalized_value":None,"normalized_unit":None,"currency":None,"conversion_rule":"normalization_failed"}
    return {"normalized_value":meta.get("value"),"normalized_unit":meta.get("unit"),"currency":meta.get("currency"),"conversion_rule":meta.get("conversion_rule")}

def normalize_observation_value(raw_value: str, unit: str) -> str | None:
    """Legacy-compatible normalizer that never assigns a currency to bare western scales."""
    return _normalization_details(raw_value,unit).get("normalized_value")

def _norm_excerpt(text: str) -> str:
    text=html.unescape(str(text)).replace("−","-").replace("–","-").replace(" "," ")
    return re.sub(r"[\s,]+","",text).lower()

def verify_observation(item: dict, source: dict) -> str | None:
    text=source.get("content_text",""); sid=source["id"]
    if item.get("source_id") != sid or item.get("source_url") != source.get("url"):
        return f"Rejected an extracted observation from source {sid}: source ID or URL did not match the active source."
    excerpt=str(item.get("excerpt") or "").strip()
    if not excerpt or excerpt not in text:
        # Whitespace/HTML entity/Unicode-minus tolerant matching, but still require a source-grounded excerpt.
        if not excerpt or _norm_excerpt(excerpt) not in _norm_excerpt(text):
            return f"Rejected an extracted observation from source {sid}: excerpt was not found as an exact source substring (whitespace/entity-tolerant matching also failed)."
    if _norm_excerpt(item.get("raw_value","")) not in _norm_excerpt(excerpt):
        return f"Rejected an extracted observation from source {sid}: raw value was absent from its excerpt."
    metric=normalize_metric(item.get("metric_name",""),item.get("unit",""))
    # Table candidates may carry period from a table header, and their excerpt includes the table header.
    normalized_excerpt=re.sub(r"[^a-z0-9]","",html.unescape(excerpt).lower())
    normalized_metric=re.sub(r"[^a-z0-9]","",str(item.get("metric_name","")).lower())
    normalized_period=re.sub(r"[^a-z0-9]","",str(item.get("period","")).lower())
    if normalized_metric not in normalized_excerpt and normalized_metric not in re.sub(r"[^a-z0-9]","",metric.lower()):
        return f"Rejected an extracted observation from source {sid}: metric label was not explicit in the cited excerpt."
    if item.get("extraction_method")=="structured_table":
        header=str(item.get("table_header_excerpt") or "")
        if not header or header not in text or normalized_period not in re.sub(r"[^a-z0-9]","",header.lower()):
            return f"Rejected an extracted observation from source {sid}: table period header was not traceable to the stored source text."
    elif normalized_period not in normalized_excerpt:
        return f"Rejected an extracted observation from source {sid}: reporting period was not explicit in the cited excerpt."
    value=parse_number(item.get("raw_value",""))
    if value is None: return f"Rejected an extracted observation from source {sid}: raw value was not a parseable number."
    if item.get("unit","").strip() in {"%","percent","per cent"}:
        limits=plausibility_range(metric)
        if limits and not (Decimal(str(limits[0])) <= value <= Decimal(str(limits[1]))):
            return f"Rejected an extracted observation from source {sid}: value is outside the plausible range for {metric}."
    return None

def _period_for_header(label: str, caption: str = "") -> dict | None:
    return parse_period(label,caption=caption) or parse_period("quarter ended "+label,caption=caption)

def _table_candidates(source: dict, max_items: int = 80) -> list[dict]:
    text=source.get("content_text") or ""; lines=text.splitlines(); out=[]; i=0
    while i<len(lines):
        if "[Table" not in lines[i]: i+=1; continue
        start=i; block=[]
        while i<len(lines) and lines[i].strip() and not (i>start and "[Table" in lines[i]):
            block.append(lines[i]); i+=1
        table_text="\n".join(block)
        rows=[(j,line) for j,line in enumerate(block) if "|" in line]
        header_candidates=[]
        for row_idx,line in rows[:5]:
            cells=[c.strip() for c in line.split("|")]
            periods=[(ci, _period_for_header(c,caption="quarter")) for ci,c in enumerate(cells)]
            periods=[(ci,p,cells[ci]) for ci,p in periods if p]
            if len(periods)>=2: header_candidates.append((row_idx,cells,periods))
        if header_candidates:
            header_idx,headers,periods=header_candidates[0]
            caption=" ".join(block[:header_idx])
            for row_idx,line in rows:
                if row_idx<=header_idx: continue
                cells=[c.strip() for c in line.split("|")]
                if len(cells)<2: continue
                metric_label=cells[0].strip(" *:•")
                raw_metric=normalize_metric(metric_label)
                if raw_metric==metric_label and raw_metric not in {"Gross NPA ratio","Net NPA ratio"} and raw_metric not in {"Gross NPA (ambiguous)","Net NPA (ambiguous)"}: continue
                for col_idx,period,period_label in periods:
                    if col_idx>=len(cells): continue
                    value_cell=cells[col_idx]
                    num_match=re.search(r"\(?\s*[-−]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)\s*\)?",value_cell)
                    if not num_match: continue
                    raw=num_match.group(0).strip().replace("−","-")
                    if "%" in value_cell or "%" in caption or ("ratio" in metric_label.lower() and "amount" not in metric_label.lower()):
                        unit="%"
                    elif re.search(r"lakh\s+crore",caption,re.I): unit="INR lakh crore"
                    elif re.search(r"\bcrores?\b|\bcr\b",caption,re.I): unit="INR crore"
                    elif re.search(r"\blakhs?\b",caption,re.I): unit="INR lakh"
                    elif re.search(r"(?:₹|\bINR\b|\bRs\.?\b)",caption,re.I) and re.search(r"million|mn|billion|bn|trillion|tn",caption,re.I):
                        currency="INR" if re.search(r"₹|\bINR\b|\bRs\.?\b",caption,re.I) else ""
                        scale=re.search(r"million|mn|billion|bn|trillion|tn",caption,re.I).group(0).lower()
                        unit=f"{currency} {scale}".strip()
                    elif re.search(r"\bUSD\b|\$|\bEUR\b|€|\bGBP\b|£",caption,re.I) and re.search(r"million|mn|billion|bn|trillion|tn",caption,re.I):
                        currency="USD" if re.search(r"\bUSD\b|\$",caption,re.I) else "EUR" if re.search(r"\bEUR\b|€",caption,re.I) else "GBP"
                        scale=re.search(r"million|mn|billion|bn|trillion|tn",caption,re.I).group(0).lower(); unit=f"{currency} {scale}"
                    elif re.search(r"million|mn|billion|bn|trillion|tn",caption,re.I): unit=re.search(r"million|mn|billion|bn|trillion|tn",caption,re.I).group(0).lower()
                    else: unit="unknown"
                    if raw_metric in {"Gross NPA (ambiguous)","Net NPA (ambiguous)"} and unit=="unknown": continue
                    excerpt=line[:700]
                    norm=_normalization_details(raw,unit,caption)
                    item={"metric_name":metric_label,"metric_canonical":normalize_metric(metric_label,unit),"metric_id":canonical_metric_id(metric_label,unit),
                          "raw_value":raw,"normalized_value":norm["normalized_value"],"normalized_unit":norm["normalized_unit"],"currency":norm["currency"],"conversion_rule":norm["conversion_rule"],"unit":unit,"period":period_label,
                          "period_start":period["start"],"period_end":period["end"],"period_type":period["period_type"],
                          "publication_date":source.get("publication_date"),"reporting_basis":"consolidated" if re.search(r"consolidated",caption,re.I) else "standalone" if re.search(r"standalone",caption,re.I) else "unknown",
                          "source_id":source["id"],"source_url":source["url"],"excerpt":excerpt,"table_header_excerpt":block[header_idx] if header_idx < len(block) else None,
                          "table_reference":next((re.search(r"\[Table[^]]*\]",x).group(0) for x in block if re.search(r"\[Table[^]]*\]",x)),None),
                          "page_reference":next((re.search(r"\[Page (\d+)\]",x).group(0) for x in block if re.search(r"\[Page (\d+)\]",x)),None),
                          "extraction_method":"structured_table","source_relevance_score":source.get("relevance_score"),"extraction_confidence":0.78,"validation_status":"pending","validation_flags":["table_period_from_header"]}
                    if not verify_observation(item,source): out.append(item)
                    if len(out)>=max_items:return out
        i=max(i,start+1)
    return out

def _heuristic_for(source: dict, limitations: list[str]) -> list[dict]:
    kept=[]
    for item in extract_heuristic(source):
        item["extraction_method"]="sentence_heuristic"
        item["source_relevance_score"]=source.get("relevance_score")
        problem=verify_observation(item,source)
        if problem: limitations.append(problem); continue
        item["publication_date"]=source.get("publication_date")
        norm=_normalization_details(item.get("raw_value",""),item.get("unit",""),item.get("excerpt", ""))
        item.update(norm)
        item["metric_canonical"]=normalize_metric(item["metric_name"],item.get("unit","")); item["metric_id"]=canonical_metric_id(item["metric_name"],item.get("unit",""))
        period=parse_period(item.get("period"))
        if period: item.update(period_start=period["start"],period_end=period["end"],period_type=period["period_type"])
        if not item["publication_date"]: item.setdefault("validation_flags",[]).append("publication_date_not_found")
        item["validation_status"]="source_excerpt_verified"; kept.append(item)
    return kept

async def extract_observations(sources: list[dict]) -> tuple[list[dict],list[str]]:
    output=[]; limitations=[]; llm_down=False
    for source in sources:
        text=source.get("content_text","")
        if not text or len(text.strip())<40: continue
        # Table-derived facts do not depend on LLM availability.
        table_items=_table_candidates(source)
        output.extend(table_items)
        # Only send financial-density windows to the LLM; retain exact original text for provenance validation.
        lines=text.splitlines(); relevant=[]
        pat=re.compile(r"advances|deposits|gross npa|net npa|gnpa|nnpa|nim|net interest income|profit after tax|operating profit|provision|capital adequacy|casa|credit cost|slippage|return on assets|return on equity",re.I)
        for idx,line in enumerate(lines):
            if pat.search(line): relevant.extend(lines[max(0,idx-1):min(len(lines),idx+2)])
        window="\n".join(dict.fromkeys(relevant))[:50000] if relevant else text[:10000]
        try:
            payload={"source_id":source["id"],"source_url":source["url"],"source_title":source.get("title",""),"publication_date":source.get("publication_date"),"text":window}
            batch=None; last_error=None
            for attempt in range(2):
                try:
                    data=await json_completion(SYSTEM,payload,max_tokens=3500)
                    batch=ObservationBatch.model_validate(data); break
                except (ValidationError,ValueError,KeyError) as exc: last_error=exc
            if batch is None: raise ValueError(f"Structured extraction failed after retry: {type(last_error).__name__}")
            for obs in batch.observations:
                item=obs.model_dump(); item["extraction_method"]="llm_candidate"
                problem=verify_observation(item,source)
                if problem: limitations.append(problem); continue
                item["publication_date"]=source.get("publication_date")
                item["metric_canonical"]=normalize_metric(item["metric_name"],item["unit"])
                item["metric_id"]=canonical_metric_id(item["metric_name"],item["unit"])
                item["source_relevance_score"]=source.get("relevance_score")
                norm=_normalization_details(item.get("raw_value",""),item.get("unit",""),item.get("excerpt", ""))
                item.update(norm)
                item["validation_status"]="source_excerpt_verified"; item["validation_flags"]=[] if item["publication_date"] else ["publication_date_not_found"]
                period=parse_period(item.get("period"))
                if period:item.update(period_start=period["start"],period_end=period["end"],period_type=period["period_type"])
                output.append(item)
        except (LLMUnavailable,RuntimeError,ValueError) as exc:
            limitations.append(f"LLM extraction unavailable for source {source['id']}: {type(exc).__name__}. Fell back to rule-based table/sentence extraction after provider failure.")
            llm_down=True
        except Exception as exc:
            limitations.append(f"Observation extraction failed for source {source['id']}: {type(exc).__name__}: {str(exc)[:120]}")
    # Rule extraction supplements LLM candidates and works when LLM calls fail.
    if llm_down:
        for source in sources:
            output.extend(_heuristic_for(source,limitations))
    unique=[]; seen=set()
    for item in output:
        key=(item.get("metric_id") or item.get("metric_canonical") or item.get("metric_name",""),item.get("raw_value"),item.get("period"),item.get("source_id"),item.get("unit"))
        if key not in seen: seen.add(key); unique.append(item)
    return unique,list(dict.fromkeys(limitations))
