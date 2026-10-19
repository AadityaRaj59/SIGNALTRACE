import json
import logging
import html
import time
import secrets
from collections import defaultdict, deque
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from backend.app.core.config import get_settings
from backend.app.db import Base, engine, get_db, ensure_schema, SessionLocal
from backend.app.models import ResearchRun, SourceRecord
from backend.app.services.research_orchestrator import create_research_run, new_run, launch_background, recover_interrupted_runs, PLANNER_ANGLES, STAGES

settings = get_settings()
ensure_schema()
logger = logging.getLogger("signaltrace")

app = FastAPI(title="SIGNALTRACE API", version="0.2.0", description="Evidence-first public financial research agent")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


# ----------------------------------------------------------------- optional auth + rate limiting
_PUBLIC_PATHS = {"/", "/api/health"}
_hits: dict[str, deque] = defaultdict(deque)

@app.middleware("http")
async def guard(request: Request, call_next):
    """Optional bearer-token auth (SIGNALTRACE_API_TOKEN) and a per-client sliding-window rate limit on expensive POSTs."""
    path = request.url.path
    current_settings = get_settings()
    if current_settings.api_token and path.startswith("/api/") and path not in _PUBLIC_PATHS:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.query_params.get("token", "")
        if not secrets.compare_digest(supplied, current_settings.api_token):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Authentication required."}, status_code=401)
    if request.method == "POST" and path == "/api/research-runs" and current_settings.rate_limit_per_minute > 0:
        client = request.client.host if request.client else "unknown"
        now = time.monotonic(); q = _hits[client]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= current_settings.rate_limit_per_minute:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": f"Rate limit reached ({current_settings.rate_limit_per_minute} runs/minute). Try again shortly."}, status_code=429, headers={"Retry-After": "30"})
        q.append(now)
    return await call_next(request)

@app.on_event("startup")
def _recover():
    with SessionLocal() as db:
        recover_interrupted_runs(db)

class ResearchCreate(BaseModel):
    company: str = Field(min_length=2, max_length=200)
    focus: str | None = Field(default=None, max_length=300)
    urls: list[str] = Field(default_factory=list, max_length=12)
    background: bool = False
    @field_validator("company")
    @classmethod
    def trim_company(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Company is required.")
        return value
    @field_validator("urls")
    @classmethod
    def trim_urls(cls, values):
        cleaned = []
        for value in values:
            value = value.strip()
            if len(value) > 2048:
                raise ValueError("Each source URL must be at most 2048 characters.")
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned

def loads_json(value: str | None, fallback=None):
    try:
        return json.loads(value or "null") or (fallback if fallback is not None else [])
    except (TypeError, json.JSONDecodeError):
        return fallback if fallback is not None else []

def run_dict(run, count=None):
    return {"id":run.id, "company_input":run.company_input, "focus":run.focus, "status":run.status,
        "created_at":run.created_at, "started_at":run.started_at, "completed_at":run.completed_at,
        "summary":run.summary, "limitations":loads_json(run.limitations_json),
        "source_count":count if count is not None else len(run.sources),
        "progress_stage":run.progress_stage, "cancel_requested":bool(run.cancel_requested)}

@app.get("/api/health")
async def health(live: bool = False):
    from backend.app.services.llm_provider import check_llm_health
    from backend.app.services.search_provider import check_search_health
    from sqlalchemy import text
    current=get_settings()
    llm=await check_llm_health(live=live)
    search=await check_search_health(live=live)
    try:
        with engine.connect() as conn: conn.execute(text("SELECT 1"))
        database={"status":"reachable","reachable":True}
    except Exception as exc: database={"status":"unavailable","reachable":False,"error_type":type(exc).__name__}
    return {"status":"ok" if database["reachable"] else "degraded","app":"SIGNALTRACE","version":"0.3.0","search":search,"llm":llm,"database":database,
        "search_configured":bool(current.tavily_api_key),"llm_configured":bool(current.openai_api_key),
        "capabilities":{"source_retrieval":True,"html_pdf_parsing":True,"llm_extraction":bool(current.openai_api_key),"contradiction_search":bool(current.tavily_api_key),"pdf_export":True,"rule_based_extraction":True,"background_runs":True,"auth_required":bool(current.api_token),"rate_limit_per_minute":current.rate_limit_per_minute},"live_checks_requested":live}

@app.get("/api/diagnostics")
async def diagnostics(live: bool = False):
    current=get_settings()
    if not current.expose_diagnostics:
        raise HTTPException(status_code=404,detail="Diagnostics are disabled. Set EXPOSE_DIAGNOSTICS=true to enable this endpoint.")
    from backend.app.core.envfile import resolve_env_file, config_source
    from backend.app.services.llm_provider import check_llm_health
    from backend.app.services.search_provider import check_search_health
    from sqlalchemy import text
    try:
        with engine.connect() as conn: conn.execute(text("SELECT 1"))
        database={"status":"reachable","backend":"sqlite" if current.database_url.startswith("sqlite") else "other"}
    except Exception as exc: database={"status":"unavailable","error_type":type(exc).__name__}
    llm=await check_llm_health(live=live)
    search=await check_search_health(live=live)
    return {"app":"SIGNALTRACE","version":"0.3.0","env_file":str(resolve_env_file()),
        "configuration_sources":{"TAVILY_API_KEY":config_source("TAVILY_API_KEY"),"OPENAI_API_KEY":config_source("OPENAI_API_KEY"),"OPENAI_BASE_URL":config_source("OPENAI_BASE_URL"),"OPENAI_MODEL":config_source("OPENAI_MODEL")},
        "search":search,"llm":llm,"database":database,"live_checks_requested":live}

@app.post("/api/research-runs")
async def start_research(payload: ResearchCreate, db: Session = Depends(get_db)):
    current=get_settings()
    # Avoid creating a doomed run when neither discovery nor manual retrieval is possible.
    if not payload.urls and not current.tavily_api_key:
        raise HTTPException(
            status_code=422,
            detail={
                "status": "needs_input",
                "message": "Add at least one public source URL or configure TAVILY_API_KEY before starting research.",
                "how_to_fix": "Paste public investor-relations, results, annual-report, or filing URLs, or configure web search.",
            },
        )
    if payload.background:
        run = new_run(db, payload.company, payload.focus)
        launch_background(run.id, payload.urls)
        return run_dict(run, 0)
    run = await create_research_run(db, payload.company, payload.focus, payload.urls)
    count = db.query(SourceRecord).filter(SourceRecord.run_id == run.id).count()
    return run_dict(run, count)

@app.get("/api/research-runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db)):
    runs = db.query(ResearchRun).order_by(ResearchRun.created_at.desc()).limit(max(1, min(limit, 100))).all()
    return [run_dict(r, db.query(SourceRecord).filter(SourceRecord.run_id == r.id).count()) for r in runs]

@app.get("/api/research-runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(404, "Research run not found.")
    return run_dict(run, db.query(SourceRecord).filter(SourceRecord.run_id == run_id).count())

@app.post("/api/research-runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(404, "Research run not found.")
    if run.status not in {"queued", "running"}:
        raise HTTPException(409, f"Run is already {run.status}.")
    run.cancel_requested = 1
    db.commit()
    return run_dict(run)

@app.delete("/api/research-runs/{run_id}")
def delete_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(404, "Research run not found.")
    if run.status in {"queued", "running"}:
        raise HTTPException(409, "Cancel the run before deleting it.")
    db.delete(run); db.commit()
    return {"deleted": run_id}

@app.get("/api/stages")
def stages():
    return {"stages": STAGES, "angles": PLANNER_ANGLES}

@app.get("/api/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db)):
    """Aggregates across saved runs for the dashboard KPI row and charts."""
    runs = db.query(ResearchRun).order_by(ResearchRun.created_at.desc()).all()
    status_counts, strengths = defaultdict(int), defaultdict(int)
    obs_total = calc_total = hyp_total = src_total = 0
    metric_counts = defaultdict(int)
    by_day = defaultdict(int)
    for r in runs:
        status_counts[r.status] += 1
        by_day[r.created_at.date().isoformat()] += 1
        obs = loads_json(r.observations_json); hyp = loads_json(r.hypotheses_json)
        obs_total += len(obs); calc_total += len(loads_json(r.calculations_json)); hyp_total += len(hyp)
        for o in obs:
            metric_counts[o.get("metric_canonical") or o.get("metric_name", "?")] += 1
        top = max((h.get("evidence_strength", "Weak") for h in hyp), key=lambda x: {"Strong":3,"Moderate":2,"Weak":1}.get(x,0), default=None)
        strengths[top or "Abstained"] += 1
    src_total = db.query(SourceRecord).count()
    return {"runs": len(runs), "status": {**dict(status_counts), "partial": status_counts.get("partial", 0) + status_counts.get("completed_with_limitations", 0)}, "strength": dict(strengths),
        "observations": obs_total, "calculations": calc_total, "hypotheses": hyp_total, "sources": src_total,
        "abstention_rate": round(strengths.get("Abstained", 0) / len(runs), 3) if runs else None,
        "top_metrics": sorted(metric_counts.items(), key=lambda x: -x[1])[:8],
        "runs_by_day": sorted(by_day.items())[-14:]}

@app.get("/api/research-runs/{run_id}/sources")
def get_sources(run_id: str, db: Session = Depends(get_db)):
    if not db.get(ResearchRun, run_id):
        raise HTTPException(404, "Research run not found.")
    rows = db.query(SourceRecord).filter(SourceRecord.run_id == run_id).order_by(SourceRecord.id).all()
    return [{"id":s.id,"url":s.url,"canonical_url":s.canonical_url,"title":s.title,"publisher":s.publisher,
        "source_type":s.source_type,"publication_date":s.publication_date,"retrieved_at":s.retrieved_at,
        "content_type":s.content_type,"content_hash":s.content_hash,"fetch_status":s.fetch_status,"parse_status":s.parse_status,"failure_reason":s.failure_reason,"relevance_score":s.relevance_score,"relevance_reasons":loads_json(s.relevance_reasons_json),"independence_group":s.independence_group,
        "excerpt":s.excerpt,"error":s.error,
        "cluster":loads_json(runrow.source_clusters_json, {}).get(str(s.id), {}) if (runrow:=db.get(ResearchRun, run_id)) else {},
        "content_chars":len(s.content_text or "")} for s in rows]

def calc_result(c):
    if c.get("result_basis_points") is not None:
        return f"{c['result_basis_points']} bps"
    if c.get("kind") == "growth_acceleration":
        return f"{c.get('result_percent')} percentage points"
    if c.get("kind") == "cagr":
        return f"{c.get('result_percent')}% CAGR"
    if c.get("kind") == "derived_ratio":
        return f"{c.get('result_percent')}% (derived)"
    return f"{c.get('result_percent')}%"

def make_report(run, sources):
    observations = loads_json(run.observations_json)
    calculations = loads_json(run.calculations_json)
    hypotheses = loads_json(run.hypotheses_json)
    audit = loads_json(run.audit_log_json)
    source_by_id = {s.id:s for s in sources}
    clusters = loads_json(run.source_clusters_json, {})
    # Attach a concrete source URL to each observation for the report's evidence ledger.
    for obs in observations:
        src = source_by_id.get(obs.get("source_id"))
        if src:
            obs["source_title"] = src.title or src.url
            obs["source_url"] = src.url
    best = max(hypotheses, key=lambda h:{"Strong":3,"Moderate":2,"Weak":1}.get(h.get("evidence_strength"),0), default=None)
    if best and best.get("evidence_strength") in {"Strong", "Moderate"}:
        discovery = best.get("statement", "Candidate hypothesis")
        strength = best.get("evidence_strength", "Weak")
        why = [best.get("rationale", "The hypothesis is linked to validated observations.")]
        why.extend(best.get("alternatives", [])[:3])
    else:
        discovery = "Insufficient evidence for a defensible discovery."
        strength = "Weak"
        why = ["The system did not promote a hypothesis above the configured evidence threshold.",
               "Retrieved source text is not itself independent corroboration.",
               "Review the observations and limitations before drawing a company-specific conclusion."]
    source_items = [{"id":s.id,"title":s.title or s.url,"url":s.url,"publisher":s.publisher,
        "source_type":s.source_type,"publication_date":s.publication_date,"retrieved_at":s.retrieved_at.isoformat(),
        "status":s.fetch_status,"parse_status":s.parse_status,"failure_reason":s.failure_reason,"relevance_score":s.relevance_score,"relevance_reasons":loads_json(s.relevance_reasons_json),"excerpt":s.excerpt,"error":s.error,
        "content_hash":s.content_hash,"independence_group":s.independence_group,"is_scanned":s.is_scanned,"ocr_required":s.ocr_required,
        "cluster":clusters.get(str(s.id), {}),"content_chars":len(s.content_text or "")} for s in sources]
    independent_clusters=len({v.get("cluster") for v in clusters.values()}) if clusters else 0
    relevant_sources=[s for s in source_items if s.get("relevance_score",0)>=3 and s.get("status")=="fetched"]
    parsed_sources=[s for s in relevant_sources if s.get("parse_status")=="parsed"]
    periods=sorted({str(o.get("period")) for o in observations if o.get("period")})
    metric_names={str(o.get("metric_canonical") or o.get("metric_name") or "").lower() for o in observations}
    analysis_sections=[]
    section_defs=[("Advances versus deposits",["advances","deposits"],["advances","deposits","credit-deposit"]),("Asset quality",["gross npa","net npa"],["npa","slippage","credit cost"]),("Profit quality",["profit after tax","net interest income","net interest margin"],["profit","income","margin","operating"]),("Funding mix",["deposits","borrowings","casa"],["deposits","borrowings","casa","credit-deposit"])]
    calc_names=[str(c.get("metric_name","")).lower() for c in calculations]
    for label,need,calc_terms in section_defs:
        present=[n for n in need if any(n in metric for metric in metric_names)]
        relevant_calc=any(any(term in name for term in calc_terms) for name in calc_names)
        section_status="supported" if len(present)==len(need) and relevant_calc else "partial" if present else "missing_data"
        explanation="Validated metrics and section-relevant calculations are available." if section_status=="supported" else "Some relevant metrics exist, but compatible calculations or required companion metrics are missing." if section_status=="partial" else "No validated observations support this section yet; no figures were imputed."
        analysis_sections.append({"name":label,"status":section_status,"available_metrics":present,"required_metrics":need,"explanation":explanation})
    timeline=sorted([{"date":s.get("publication_date"),"title":s.get("title"),"url":s.get("url"),"source_id":s.get("id")} for s in relevant_sources if s.get("publication_date")],key=lambda x:str(x.get("date")))
    coverage={"sources_discovered":len(source_items),"relevant_sources":len(relevant_sources),"parsed_relevant_sources":len(parsed_sources),"validated_observations":len(observations),"deterministic_calculations":len(calculations),"candidate_signals":len(hypotheses),"independent_source_clusters":independent_clusters,"reporting_periods":periods}
    return {"company":run.company_input,"focus":run.focus,"run_id":run.id,
        "generated_at":run.completed_at.isoformat() if run.completed_at else None,"discovery":discovery,
        "why_it_matters":why,"sources":source_items,"observations":observations,"calculations":calculations,
        "hypotheses":hypotheses,"timeline":timeline,"analysis_sections":analysis_sections,"coverage":coverage,"audit_log":audit,
        "counterargument":best.get("counter_evidence_rationale") or best.get("counterargument", "The available evidence does not yet establish that alternative explanations have been eliminated.") if best else "There are not enough validated financial observations to test a company-specific hypothesis.",
        "alternative_explanations":(best.get("alternatives",[]) if best else []),
        "falsification_condition":best.get("falsification_condition", "Reconsider only after new dated, comparable observations materially change the evidence base.") if best else "Reconsider this abstention only when dated, comparable observations from traceable sources support a specific hypothesis and counter-evidence has been examined.",
        "evidence_strength":strength,"limitations":loads_json(run.limitations_json),
        "status":run.status,"summary":run.summary,"research_plan":loads_json(run.research_plan_json),
        "independent_clusters":independent_clusters,
        "confidence_methodology":"Confidence is based on source relevance and credibility, excerpt provenance, extraction confidence, period/unit/basis compatibility, deterministic calculation validity, independent source clusters, and unresolved contradictions. Search-result presence alone is not corroboration.",
        "methodology":"Public-source retrieval, source relevance filtering, source-excerpt verification, deterministic calculations, deterministic signal rules, counter-evidence review where available, and explicit uncertainty. LLM extraction is not a substitute for validation.",
        "disclaimer":"For research and educational purposes only. Not investment advice."}

@app.get("/api/research-runs/{run_id}/stages")
def get_run_stages(run_id: str, db: Session = Depends(get_db)):
    run=db.get(ResearchRun,run_id)
    if not run: raise HTTPException(404,"Research run not found.")
    return {"run_id":run_id,"status":run.status,"stages":loads_json(run.stages_json)}

@app.get("/api/research-runs/{run_id}/observations")
def get_run_observations(run_id: str, db: Session = Depends(get_db)):
    run=db.get(ResearchRun,run_id)
    if not run: raise HTTPException(404,"Research run not found.")
    sources=db.query(SourceRecord).filter(SourceRecord.run_id==run_id).all()
    return {"run_id":run_id,"observations":make_report(run,sources)["observations"]}

@app.get("/api/research-runs/{run_id}/calculations")
def get_run_calculations(run_id: str, db: Session = Depends(get_db)):
    run=db.get(ResearchRun,run_id)
    if not run: raise HTTPException(404,"Research run not found.")
    return {"run_id":run_id,"calculations":loads_json(run.calculations_json)}

@app.get("/api/research-runs/{run_id}/signals")
def get_run_signals(run_id: str, db: Session = Depends(get_db)):
    run=db.get(ResearchRun,run_id)
    if not run: raise HTTPException(404,"Research run not found.")
    return {"run_id":run_id,"signals":loads_json(run.hypotheses_json)}

@app.get("/api/research-runs/{run_id}/audit")
def get_run_audit(run_id: str, db: Session = Depends(get_db)):
    run=db.get(ResearchRun,run_id)
    if not run: raise HTTPException(404,"Research run not found.")
    return {"run_id":run_id,"audit":loads_json(run.audit_log_json),"limitations":loads_json(run.limitations_json)}

@app.get("/api/research-runs/{run_id}/report")
def get_report(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(404, "Research run not found.")
    sources = db.query(SourceRecord).filter(SourceRecord.run_id == run_id).all()
    return make_report(run, sources)

@app.get("/api/research-runs/{run_id}/events")
def get_events(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(404, "Research run not found.")
    return loads_json(run.audit_log_json)

@app.get("/api/research-runs/{run_id}/export.html", response_class=HTMLResponse)
def export_html(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(404, "Research run not found.")
    sources = db.query(SourceRecord).filter(SourceRecord.run_id == run_id).all()
    report = make_report(run, sources)
    esc = lambda value: html.escape(str(value or ""), quote=True)
    source_html = "".join(f'<article class="source"><h3>{esc(s["title"])}</h3><p><a href="{esc(s["url"])}" rel="noopener noreferrer">{esc(s["url"])}</a></p><p>{esc(s["excerpt"])}</p><small>Status: {esc(s["status"])} · Parse: {esc(s.get("parse_status"))} · Relevance: {esc(s.get("relevance_score"))} · Publisher: {esc(s.get("publisher"))} · Independence group: {esc(s.get("independence_group"))} · Content hash: {esc(s.get("content_hash"))} · Retrieved: {esc(s["retrieved_at"])}</small><p>{esc(", ".join(s.get("relevance_reasons") or []))}</p></article>' for s in report["sources"]) or "<p>No sources collected.</p>"
    limitations = "".join(f"<li>{esc(x)}</li>" for x in report["limitations"])
    observations = "".join(f'<tr><td>{esc(o.get("metric_name"))}</td><td>{esc(o.get("raw_value"))} {esc(o.get("unit"))}</td><td>{esc(o.get("normalized_value") or "not normalized")} {esc(o.get("normalized_unit") or "")}</td><td>{esc(o.get("conversion_rule") or "not available")}</td><td>{esc(o.get("period"))}</td><td>{esc(o.get("reporting_basis"))}</td><td><a href="{esc(o.get("source_url"))}">Source #{esc(o.get("source_id"))}</a> · {esc(o.get("page_reference") or "")} {esc(o.get("table_reference") or "")}<blockquote>{esc((str(o.get("table_header_excerpt") or "") + " | " if o.get("table_header_excerpt") else "") + str(o.get("excerpt") or ""))}</blockquote></td></tr>' for o in report["observations"])
    calculations = "".join(f'<tr><td>{esc(c.get("metric_name"))}</td><td>{esc(c.get("formula"))}</td><td>{esc(calc_result(c))}</td><td>{esc(c.get("start_period"))} → {esc(c.get("end_period"))}</td></tr>' for c in report["calculations"])
    coverage_html="".join(f"<li>{esc(k.replace('_',' ').title())}: {esc(v if not isinstance(v,list) else ', '.join(v) or 'none')}</li>" for k,v in report["coverage"].items())
    sections_html="".join(f"<article class='source'><h3>{esc(x['name'])} · {esc(x['status'])}</h3><p>{esc(x['explanation'])}</p><p>Available metrics: {esc(', '.join(x['available_metrics']) or 'none')}</p></article>" for x in report["analysis_sections"])
    timeline_html="".join(f"<li>{esc(x['date'])} — <a href='{esc(x['url'])}'>{esc(x['title'])}</a></li>" for x in report["timeline"])
    signals_html="".join(f"<article class='source'><h3>{esc(h.get('statement'))}</h3><p>{esc(h.get('rationale'))}</p><p>Evidence strength: {esc(h.get('evidence_strength','Weak'))}</p><p>Supporting observations: {esc(', '.join(h.get('supporting_observation_ids',[])))}</p><p>Counter-evidence: {esc(h.get('counter_evidence_rationale') or 'Not established')}</p><p>Falsification: {esc(h.get('falsification_condition'))}</p></article>" for h in report["hypotheses"])
    html_doc = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>SIGNALTRACE Report - {esc(report["company"])}</title><style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1000px;margin:36px auto;padding:0 20px;color:#17243b}}h1,h2{{color:#12304a}}.notice{{padding:16px;background:#eef7f6;border-left:4px solid #188c86}}.source{{border:1px solid #dbe3ec;border-radius:12px;padding:16px;margin:12px 0;overflow-wrap:anywhere}}a{{color:#087d79}}small{{color:#526174}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid #dbe3ec;padding:8px;text-align:left;overflow-wrap:anywhere}}blockquote{{margin:6px 0;padding:8px;border-left:3px solid #087e79;background:#f5f8fb;font-size:13px}}</style></head><body>
<h1>SIGNALTRACE Research Report</h1><p><b>Company:</b> {esc(report["company"])} · <b>Run:</b> {esc(report["run_id"])} · <b>Generated:</b> {esc(report["generated_at"])}</p><div class="notice"><h2>The Discovery</h2><p>{esc(report["discovery"])}</p><p><b>Evidence strength:</b> {esc(report["evidence_strength"])}</p></div><h2>Why it matters</h2><ul>{''.join(f'<li>{esc(x)}</li>' for x in report["why_it_matters"])}</ul><h2>Data coverage</h2><ul>{coverage_html}</ul><h2>Analysis sections</h2>{sections_html}<h2>Ranked signals</h2>{signals_html}<h2>Disclosure timeline</h2><ul>{timeline_html or "<li>No dated source disclosures were identified.</li>"}</ul>
<h2>Validated observations</h2><table><tr><th>Metric</th><th>Raw value / unit</th><th>Normalized value</th><th>Conversion rule</th><th>Period</th><th>Basis</th><th>Provenance / excerpt</th></tr>{observations}</table><h2>Calculations</h2><table><tr><th>Metric</th><th>Formula</th><th>Result</th><th>Periods</th></tr>{calculations}</table><h2>Sources and provenance</h2>{source_html}<h2>Counterargument</h2><p>{esc(report["counterargument"])}</p><h2>Alternative explanations</h2><ul>{''.join(f'<li>{esc(x)}</li>' for x in report["alternative_explanations"]) or "<li>None were validated.</li>"}</ul><h2>Confidence methodology</h2><p>{esc(report["confidence_methodology"])}</p><h2>Falsification condition</h2><p>{esc(report["falsification_condition"])}</p><h2>Limitations</h2><ul>{limitations}</ul><p>{esc(report["methodology"])}</p><hr><p>{esc(report["disclaimer"])}</p></body></html>'''
    return HTMLResponse(html_doc)

@app.get("/api/research-runs/{run_id}/export.pdf")
def export_pdf(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(404, "Research run not found.")
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    sources = db.query(SourceRecord).filter(SourceRecord.run_id == run_id).all()
    report = make_report(run, sources)
    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=17*mm, leftMargin=17*mm, topMargin=16*mm, bottomMargin=16*mm, title=f"SIGNALTRACE — {run.company_input}")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="STTitle", parent=styles["Title"], textColor=colors.HexColor("#12304a"), alignment=TA_LEFT, fontSize=21, leading=26, spaceAfter=8))
    styles.add(ParagraphStyle(name="STHeading", parent=styles["Heading2"], textColor=colors.HexColor("#087e79"), spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="STSmall", parent=styles["BodyText"], fontSize=8, leading=10, textColor=colors.HexColor("#526174")))
    def para(value, style="BodyText"):
        return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), styles[style])
    def link_para(url):
        safe = escape(str(url or ""), {"\"": "&quot;"})
        return Paragraph(f'<link href="{safe}" color="#087d79">{escape(str(url or ""))}</link>', styles["STSmall"])
    story = [para("SIGNALTRACE Research Report", "STTitle"), para(f"Company: {run.company_input}"), para(f"Run ID: {run.id}"), para(f"Generated: {report['generated_at'] or 'unknown'}"), Spacer(1, 8)]
    story += [para("The Discovery", "STHeading"), para(report["discovery"]), para(f"Evidence strength: {report['evidence_strength']}"), para("Why it matters", "STHeading")]
    story += [para("• " + item) for item in report["why_it_matters"]]
    story += [para("Data coverage", "STHeading")]
    story += [para(f"{k.replace('_',' ').title()}: {', '.join(v) if isinstance(v,list) else v}", "STSmall") for k,v in report["coverage"].items()]
    story += [para("Analysis sections", "STHeading")]
    story += [para(f"{x['name']} — {x['status']}: {x['explanation']}", "STSmall") for x in report["analysis_sections"]]
    story += [para("Ranked signals", "STHeading")]
    if report["hypotheses"]:
        for h in report["hypotheses"]:
            story += [para(h.get("statement"),"Heading3"),para(h.get("rationale")),para(f"Evidence strength: {h.get('evidence_strength','Weak')} · Supporting observations: {h.get('supporting_observation_ids',[])} · Calculations: {h.get('calculation_ids',[])}","STSmall"),para(f"Counter-evidence: {h.get('counter_evidence_rationale') or 'Not established'}","STSmall"),para(f"Falsification: {h.get('falsification_condition')}","STSmall")]
    else: story.append(para("No signal passed the evidence gate; no discovery was fabricated.","STSmall"))
    story += [para("Disclosure timeline", "STHeading")]
    story += [para(f"{x['date']} — {x['title']} — {x['url']}","STSmall") for x in report["timeline"]] or [para("No dated source disclosures were identified.","STSmall")]
    story += [para("Validated observations", "STHeading")]
    obs_rows = [[para("Metric", "STSmall"), para("Value / Unit", "STSmall"), para("Period", "STSmall"), para("Basis", "STSmall"), para("Page / Table", "STSmall"), para("Source ID", "STSmall")]]
    for obs in report["observations"]:
        page_table=" ".join(str(x) for x in (obs.get("page_reference"),obs.get("table_reference")) if x)
        obs_rows.append([para(obs.get("metric_name"), "STSmall"), para(f"{obs.get('raw_value')} {obs.get('unit')}\nNormalized: {obs.get('normalized_value') or 'not normalized'} {obs.get('normalized_unit') or ''}\nRule: {obs.get('conversion_rule') or 'not available'}", "STSmall"), para(obs.get("period"), "STSmall"), para(obs.get("reporting_basis"), "STSmall"), para(page_table or "—", "STSmall"), para(obs.get("source_id"), "STSmall")])
    if len(obs_rows) == 1:
        obs_rows.append([para("No validated observations", "STSmall"), "", "", "", "", ""])
    table = Table(obs_rows, colWidths=[34*mm, 35*mm, 26*mm, 22*mm, 28*mm, 20*mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e7f4f2")),("GRID",(0,0),(-1,-1),0.35,colors.HexColor("#d4dde5")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    story += [table, para("Calculations", "STHeading")]
    for calc in report["calculations"]:
        story.append(para(f"{calc.get('metric_name')}: {calc.get('formula')} = {calc_result(calc)} ({calc.get('start_period')} to {calc.get('end_period')}); input observations: {calc.get('input_observation_ids')}"))
    story += [para("Sources and provenance", "STHeading")]
    for src in report["sources"]:
        story.extend([para(src.get("title") or src.get("url"), "Heading4"), link_para(src.get("url")), para(f"Publisher: {src.get('publisher')} | Status: {src.get('status')} | Parse: {src.get('parse_status')} | Relevance: {src.get('relevance_score')} | Independence group: {src.get('independence_group')} | Content hash: {src.get('content_hash')} | Retrieved: {src.get('retrieved_at')}", "STSmall"), para(src.get("excerpt", ""), "STSmall"), Spacer(1, 5)])
    story += [para("Counterargument", "STHeading"), para(report["counterargument"]), para("Alternative explanations", "STHeading")]
    story += [para("• " + x) for x in report["alternative_explanations"]] or [para("No alternative explanations were validated.","STSmall")]
    story += [para("Confidence methodology", "STHeading"), para(report["confidence_methodology"]), para("Falsification condition", "STHeading"), para(report["falsification_condition"]), para("Limitations", "STHeading")]
    story += [para("• " + item) for item in report["limitations"]]
    story += [Spacer(1, 8), para(report["disclaimer"], "STSmall")]
    def page_footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d4dde5")); canvas.line(17*mm, 12*mm, A4[0]-17*mm, 12*mm)
        canvas.setFont("Helvetica", 8); canvas.setFillColor(colors.HexColor("#526174"))
        canvas.drawString(17*mm, 7*mm, f"SIGNALTRACE · {run.company_input}")
        canvas.drawRightString(A4[0]-17*mm, 7*mm, f"Page {document.page}")
        canvas.restoreState()
    doc.build(story,onFirstPage=page_footer,onLaterPages=page_footer)
    return Response(content=stream.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="signaltrace-{run_id}.pdf"'})

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8"))
