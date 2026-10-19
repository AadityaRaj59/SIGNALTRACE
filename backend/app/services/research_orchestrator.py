import json
import asyncio
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy.orm import Session
from backend.app.core.config import get_settings
from backend.app.models import ResearchRun, SourceRecord
from backend.app.services.search_provider import search_public_sources
from backend.app.services.source_fetcher import fetch_public_url, FetchResult
from backend.app.services.financial_extraction import extract_observations
from backend.app.services.source_independence import cluster_sources
from backend.app.services.evidence_engine import PLANNER_ANGLES, calculate_observation_trends, build_hypotheses, challenge_hypotheses, score_hypothesis, evaluate_counter_evidence
from backend.app.services.query_planner import plan_queries
from backend.app.services.source_scoring import score_source, independence_group


class RunCancelled(Exception):
    pass

STAGES = ["planning", "source_discovery", "retrieval", "extraction", "analysis", "hypotheses", "skeptic", "report"]

def _checkpoint(db: Session, run: ResearchRun, events: list[dict], stage: str) -> None:
    """Persist progress and honour cooperative cancellation between pipeline stages."""
    run.progress_stage = stage
    run.audit_log_json = json.dumps(events, ensure_ascii=False)
    db.commit(); db.refresh(run)
    if run.cancel_requested:
        raise RunCancelled()

def audit_event(events: list[dict], stage: str, message: str, details: dict | None = None) -> None:
    events.append({"timestamp": datetime.now(timezone.utc).isoformat(), "stage": stage, "message": message, "details": details or {}})

async def _discover_urls(company: str, focus: str | None, manual_urls: list[str], limitations: list[str], events: list[dict]) -> list[dict]:
    settings=get_settings()
    candidates=[{"url":u,"title":"User-supplied public URL","source_type":"manual","relevance":score_source(u,"", "", company)} for u in manual_urls]
    if not settings.tavily_api_key:
        limitations.append("TAVILY_API_KEY is not configured in the settings loaded by this process; public search was not attempted. Manual URLs are still evaluated for relevance.")
        return candidates[:settings.max_research_sources]
    seen={u["url"] for u in candidates}
    import re
    ticker_match=re.search(r"\b(?:NSE|BSE)\s*:\s*([A-Z0-9]+)",company,re.I)
    company_name=re.sub(r"\s*\((?:NSE|BSE)\s*:\s*[A-Z0-9]+\)\s*", " ", company, flags=re.I).strip()
    query_plan=plan_queries(company_name,focus,ticker=ticker_match.group(1) if ticker_match else None)
    failures=0
    for query_index,entry in enumerate(query_plan[:max(1,settings.max_search_queries)]):
        try:
            results=await search_public_sources(entry["query"],limit=settings.max_results_per_query,include_domains=entry.get("include_domains"))
            audit_event(events,"source_discovery","Executed focused financial-source search",{"angle":entry["angle"],"result_count":len(results)})
            for item in results:
                url=item.get("url")
                if not url or url in seen: continue
                seen.add(url)
                relevance=score_source(url,item.get("title",""),item.get("content",""),company)
                candidates.append({**item,"source_type":relevance["source_type"],"relevance":relevance,"query_angle":entry["angle"]})
            # Escalate to exchange/regulatory/narrative queries if the initial search tier is weak.
            if query_index == min(5, len(query_plan)-1):
                relevant_count=sum(1 for candidate in candidates if (candidate.get("relevance") or {}).get("relevant"))
                if relevant_count < 3:
                    audit_event(events,"source_discovery","Initial search tier was weak; expanding to exchange, regulatory and contradiction-oriented queries",{"relevant_candidate_count":relevant_count})
                elif relevant_count >= 6:
                    audit_event(events,"source_discovery","Initial search tier produced sufficient relevant candidates; limiting additional search calls",{"relevant_candidate_count":relevant_count})
                    break
        except Exception as exc:
            failures+=1
            limitations.append(f"Search stage {entry['angle']} failed: {type(exc).__name__}: {str(exc)[:140]}")
            audit_event(events,"source_discovery","Search query failed",{"angle":entry["angle"],"error_type":type(exc).__name__})
            # Authentication, billing and missing-credential failures are not repaired by repeating the same provider call.
            if type(exc).__name__ in {"SearchAuthError","SearchQuotaError"}: break
    if failures and not any(c.get("source_type")!="manual" for c in candidates):
        limitations.append("No search results were acquired because the search provider failed; this is a technical limitation, not evidence of absence.")
    # Obvious homepages are retained for auditability but sorted after relevant filing candidates.
    candidates.sort(key=lambda c:(not bool((c.get("relevance") or {}).get("relevant")), -float((c.get("relevance") or {}).get("score",0))))
    if not candidates: limitations.append("No public source URLs were supplied or discovered.")
    return candidates[:settings.max_research_sources]

def new_run(db: Session, company: str, focus: str | None, status: str = "queued") -> ResearchRun:
    run = ResearchRun(id=str(uuid4()), company_input=company, focus=focus, status=status,
        summary="Research queued. The system will not assert a discovery without validated evidence.", limitations_json="[]")
    db.add(run); db.commit(); db.refresh(run)
    return run

async def create_research_run(db: Session, company: str, focus: str | None, urls: list[str]) -> ResearchRun:
    """Synchronous path: create and execute in the request lifecycle."""
    run = new_run(db, company, focus)
    return await execute_run(db, run.id, urls)

async def execute_run(db: Session, run_id: str, urls: list[str]) -> ResearchRun:
    run = db.get(ResearchRun, run_id)
    company, focus = run.company_input, run.focus
    run.status = "running"; run.started_at = datetime.now(timezone.utc)
    run.summary = "Research started. The system will not assert a discovery without validated evidence."
    db.commit()
    limitations: list[str] = []
    events: list[dict] = []
    run.research_plan_json = json.dumps(PLANNER_ANGLES)
    settings=get_settings()
    from backend.app.core.envfile import resolve_env_file, config_source
    run.config_snapshot_json=json.dumps({"env_file":str(resolve_env_file()),"tavily_configured":bool(settings.tavily_api_key),"tavily_key_source":config_source("TAVILY_API_KEY"),"llm_configured":bool(settings.openai_api_key),"llm_key_source":config_source("OPENAI_API_KEY"),"llm_model":settings.openai_model,"llm_base_url":settings.openai_base_url,"database_backend":"sqlite" if settings.database_url.startswith("sqlite") else "other"},ensure_ascii=False)
    stage_records=[]
    audit_event(events, "planning", "Selected Track A research angles", {"angles": len(PLANNER_ANGLES), "focus": focus})
    try:
        _checkpoint(db, run, events, "source_discovery")
        candidates = await _discover_urls(company, focus, urls, limitations, events)
        _checkpoint(db, run, events, "retrieval")
        seen_urls, seen_hashes = set(), set()
        concurrency=max(1,min(int(settings.fetch_concurrency),8))
        semaphore=asyncio.Semaphore(concurrency)
        async def bounded_fetch(candidate):
            async with semaphore:
                return await fetch_public_url(candidate["url"])
        for batch_start in range(0,len(candidates),concurrency):
            _checkpoint(db,run,events,"retrieval")
            batch=candidates[batch_start:batch_start+concurrency]
            results=await asyncio.gather(*(bounded_fetch(candidate) for candidate in batch),return_exceptions=True)
            for candidate,fetched_result in zip(batch,results):
                if isinstance(fetched_result,Exception):
                    fetched=FetchResult(candidate["url"],candidate["url"],status="error",error=f"Fetch failed: {type(fetched_result).__name__}",failure_reason="fetch_failure")
                else:
                    fetched=fetched_result
                if fetched.canonical_url in seen_urls:
                    limitations.append(f"Duplicate canonical URL skipped: {fetched.canonical_url}")
                    continue
                seen_urls.add(fetched.canonical_url)
                if fetched.content_hash and fetched.content_hash in seen_hashes:
                    limitations.append(f"Identical content hash skipped as duplicate evidence: {fetched.canonical_url}")
                    continue
                if fetched.content_hash: seen_hashes.add(fetched.content_hash)
                relevance=score_source(fetched.url,fetched.title or candidate.get("title", ""),fetched.text,company)
                fetch_status=fetched.status
                failure_reason=fetched.failure_reason or ("parser_failure" if fetched.status=="partial" else None)
                source_error=fetched.error
                if fetched.status == "fetched" and (relevance["is_homepage"] or not relevance["relevant"]):
                    fetch_status="irrelevant"
                    failure_reason="irrelevant_page"
                    source_error="Page was fetched but failed the financial-source relevance gate; generic homepages/navigation are not evidence."
                    limitations.append(f"Rejected irrelevant source as evidence: {fetched.canonical_url} (score {relevance['score']}; reasons: {', '.join(relevance['reasons']) or 'no financial relevance'}).")
                if fetched.error: limitations.append(f"{fetched.canonical_url}: {fetched.error}")
                source=SourceRecord(run_id=run.id,url=fetched.url,canonical_url=fetched.canonical_url,
                    title=(fetched.title or candidate.get("title") or "")[:500],publisher=fetched.publisher,
                    source_type=candidate.get("source_type",relevance["source_type"]),publication_date=fetched.publication_date,
                    content_type=fetched.content_type,content_hash=fetched.content_hash,fetch_status=fetch_status,
                    excerpt=fetched.text[:3000],content_text=fetched.text,error=source_error,
                    parse_status=fetched.parse_status or ("parsed" if fetched.status=="fetched" else "parse_failure" if fetched.status=="partial" else "not_parsed"),
                    is_scanned=fetched.is_scanned,ocr_required=fetched.ocr_required,failure_reason=failure_reason,
                    relevance_score=relevance["score"],relevance_reasons_json=json.dumps(relevance["reasons"]),
                    independence_group=independence_group(fetched.url,fetched.title))
                db.add(source)
        db.commit()
        sources = db.query(SourceRecord).filter(SourceRecord.run_id == run.id).all()
        source_payload = [{"id":s.id,"url":s.url,"title":s.title,"publisher":s.publisher,
            "publication_date":s.publication_date,"content_text":s.content_text,"fetch_status":s.fetch_status,"relevance_score":s.relevance_score} for s in sources if s.fetch_status == "fetched" and s.relevance_score >= 3]
        audit_event(events, "retrieval", "Finished fetching, parsing and relevance scoring source candidates", {"sources_saved":len(sources),"relevant_readable_sources":len(source_payload)})
        stage_records.append({"stage":"source_retrieval_and_relevance","status":"completed" if source_payload else "failed","timestamp":datetime.now(timezone.utc).isoformat(),"source_count":len(sources),"relevant_count":len(source_payload)})
        run.stages_json=json.dumps(stage_records); db.commit()
        _checkpoint(db, run, events, "extraction")
        observations, extraction_limits = await extract_observations(source_payload)
        limitations.extend(extraction_limits)
        for i, obs in enumerate(observations, start=1):
            obs["id"] = f"obs_{i}"
        calculations, calc_limits = calculate_observation_trends(observations)
        limitations.extend(calc_limits)
        audit_event(events, "analysis", "Extracted and validated candidate observations", {"observations":len(observations),"calculations":len(calculations)})
        stage_records.append({"stage":"extract_and_calculate","status":"completed" if observations else "completed_with_limitations","timestamp":datetime.now(timezone.utc).isoformat(),"observations":len(observations),"calculations":len(calculations)})
        run.stages_json=json.dumps(stage_records); db.commit()
        _checkpoint(db, run, events, "hypotheses")
        hypotheses, hypothesis_limits = await build_hypotheses(company, focus, observations, calculations)
        limitations.extend(hypothesis_limits)
        hypotheses, counter_limits = await challenge_hypotheses(hypotheses, company)
        limitations.extend(counter_limits)
        _checkpoint(db, run, events, "skeptic")
        for h in hypotheses:
            await score_hypothesis(h, observations, calculations)
        # Counter-search result pages are recorded as research leads, not automatically counted as evidence.
        counter_urls = []
        for h in hypotheses:
            for item in h.get("counter_search_results", []):
                if item.get("url") and item["url"] not in seen_urls:
                    counter_urls.append(item)
                    seen_urls.add(item["url"])
        for item in counter_urls[:5]:
            fetched = await fetch_public_url(item["url"])
            if fetched.content_hash and fetched.content_hash in seen_hashes:
                continue
            if fetched.content_hash:
                seen_hashes.add(fetched.content_hash)
            counter_relevance=score_source(fetched.url,fetched.title or item.get("title", ""),fetched.text,company)
            counter_status="irrelevant" if fetched.status=="fetched" and (not counter_relevance["relevant"] or counter_relevance["is_homepage"]) else fetched.status
            counter_error=fetched.error or ("Counter-search lead was not relevant enough to count as evidence." if counter_status=="irrelevant" else None)
            db.add(SourceRecord(run_id=run.id, url=fetched.url, canonical_url=fetched.canonical_url,
                title=(fetched.title or item.get("title") or "")[:500], publisher=fetched.publisher,
                source_type="contradiction_search_lead", publication_date=fetched.publication_date,
                content_type=fetched.content_type, content_hash=fetched.content_hash, fetch_status=counter_status,
                excerpt=fetched.text[:3000], content_text=fetched.text, error=counter_error,
                parse_status=fetched.parse_status or ("parsed" if fetched.status=="fetched" else "not_parsed"),
                is_scanned=fetched.is_scanned,ocr_required=fetched.ocr_required,failure_reason=fetched.failure_reason or ("irrelevant_page" if counter_status=="irrelevant" else None),
                relevance_score=counter_relevance["score"],relevance_reasons_json=json.dumps(counter_relevance["reasons"]),
                independence_group=independence_group(fetched.url,fetched.title)))
        db.flush()
        if counter_urls:
            db.flush()
            counter_sources = db.query(SourceRecord).filter(SourceRecord.run_id == run.id, SourceRecord.source_type == "contradiction_search_lead", SourceRecord.fetch_status == "fetched", SourceRecord.relevance_score >= 3).all()
            counter_ids = {source.id for source in counter_sources}
            counter_payload = [{"id":s.id,"url":s.url,"title":s.title,"publisher":s.publisher,
                "publication_date":s.publication_date,"content_text":s.content_text,"fetch_status":s.fetch_status,"relevance_score":s.relevance_score} for s in counter_sources]
            counter_observations, counter_extraction_limits = await extract_observations(counter_payload)
            limitations.extend(counter_extraction_limits)
            for obs in counter_observations:
                obs["id"] = f"obs_{len(observations)+1}"
                observations.append(obs)
            hypotheses, counter_eval_limits = await evaluate_counter_evidence(hypotheses, observations, counter_ids)
            limitations.extend(counter_eval_limits)
            audit_event(events, "skeptic", "Evaluated observations from contradiction-search pages", {"candidate_pages":len(counter_sources),"counter_observations":len(counter_observations)})
        # Independence clustering over every fetched source (supporting and counter-search).
        all_fetched = [{"id":s.id,"url":s.url,"content_text":s.content_text} for s in db.query(SourceRecord).filter(SourceRecord.run_id == run.id, SourceRecord.fetch_status == "fetched").all()]
        clusters = cluster_sources(all_fetched)
        run.source_clusters_json = json.dumps({str(k): v for k, v in clusters.items()})
        audit_event(events, "independence", "Clustered sources by publisher domain and near-duplicate text", {"sources":len(all_fetched),"independent_clusters":len({v["cluster"] for v in clusters.values()})})
        # Score only after opposing observations from counter-search pages have been evaluated.
        for h in hypotheses:
            await score_hypothesis(h, observations, calculations, clusters)
        # Store only user-facing rationale, IDs and evidence, never private chain-of-thought.
        run.observations_json = json.dumps(observations, ensure_ascii=False)
        run.calculations_json = json.dumps(calculations, ensure_ascii=False)
        run.hypotheses_json = json.dumps(hypotheses, ensure_ascii=False)
        run.audit_log_json = json.dumps(events, ensure_ascii=False)
        all_sources=db.query(SourceRecord).filter(SourceRecord.run_id==run.id).all()
        relevant_sources=[s for s in all_sources if s.fetch_status=="fetched" and s.relevance_score>=3 and len(s.content_text or "")>100]
        readable=[s for s in relevant_sources if s.parse_status=="parsed"]
        clusters_now=cluster_sources([{"id":s.id,"url":s.url,"content_text":s.content_text} for s in readable])
        independent_count=len({v.get("cluster") for v in clusters_now.values()}) if clusters_now else 0
        technical_failure=(not relevant_sources or any(s.fetch_status in {"error","partial"} for s in all_sources)
            or any(("LLM extraction unavailable" in item or "Search stage" in item or "Search query failed" in item) for item in limitations))
        if hypotheses:
            top=max(hypotheses,key=lambda h:{"Strong":3,"Moderate":2,"Weak":1}.get(h.get("evidence_strength"),0))
            if top.get("evidence_strength") in {"Strong","Moderate"} and len(top.get("supporting_observation_ids",[]))>=2 and independent_count>=2 and calculations:
                run.summary=f"Generated {len(hypotheses)} deterministic evidence-linked candidate signal(s); the leading candidate passed the current evidence gate, subject to listed limitations."
                run.status="completed"
            else:
                run.summary=f"Generated {len(hypotheses)} candidate signal(s), but independent corroboration or calculation coverage is incomplete."
                run.status="completed_with_limitations"
                limitations.append("Candidate signals remain limited by corroboration, source coverage, or calculation completeness.")
        elif observations:
            run.summary=f"Validated {len(observations)} source-grounded financial observation(s) and {len(calculations)} calculation(s), but no deterministic signal rule met the evidence threshold."
            run.status="completed_with_limitations" if calculations or readable else "abstained"
            limitations.append("No defensible cross-metric signal met the configured evidence threshold; the report does not invent a discovery.")
        elif readable and not technical_failure:
            run.summary="Relevant financial documents were parsed, but no observations passed provenance and financial validation."
            run.status="abstained"
            limitations.append("Relevant documents were readable, but no defensible observation could be validated. Inspect per-source extraction failures.")
        else:
            run.summary="Research failed to acquire and parse relevant financial evidence. Review provider diagnostics and source failure reasons, then retry."
            run.status="failed"
            limitations.append("No relevant parsed financial sources were available; this is a source acquisition/relevance failure, not a defensible research abstention.")
        audit_event(events,"completeness_audit","Evaluated report completeness",{"relevant_sources":len(relevant_sources),"readable_relevant_sources":len(readable),"observations":len(observations),"calculations":len(calculations),"independent_source_clusters":independent_count,"status":run.status})
        stage_records.append({"stage":"completeness_audit","status":run.status,"timestamp":datetime.now(timezone.utc).isoformat(),"relevant_sources":len(relevant_sources),"observations":len(observations),"calculations":len(calculations),"independent_source_clusters":independent_count})
        # Persist a stable stage inventory even when a stage could not run.
        completed_names={x.get("stage") for x in stage_records if x.get("status") in {"completed","completed_with_limitations",run.status}}
        now=datetime.now(timezone.utc).isoformat()
        stage_inventory=[
            {"stage":"input_validation","status":"completed","timestamp":now},
            {"stage":"company_identity_and_plan","status":"completed","timestamp":now},
            {"stage":"query_planning_and_discovery","status":"completed" if candidates else "failed","timestamp":now,"candidate_count":len(candidates)},
            {"stage":"relevance_filter","status":"completed" if relevant_sources else "failed","timestamp":now,"relevant_source_count":len(relevant_sources)},
            {"stage":"fetch_and_parse","status":"completed" if readable else "failed","timestamp":now,"readable_source_count":len(readable)},
            {"stage":"financial_extraction_and_validation","status":"completed" if observations else "completed_with_limitations" if readable else "failed","timestamp":now,"observation_count":len(observations)},
            {"stage":"deterministic_calculations","status":"completed" if calculations else "completed_with_limitations","timestamp":now,"calculation_count":len(calculations)},
            {"stage":"deterministic_signal_generation","status":"completed" if hypotheses else "abstained","timestamp":now,"signal_count":len(hypotheses)},
            {"stage":"counter_evidence_and_scoring","status":"completed_with_limitations" if any("counter" in x.lower() for x in limitations) else "completed","timestamp":now},
            {"stage":"report_generation_and_validation","status":run.status,"timestamp":now,"audit_passed":run.status=="completed"},
        ]
        run.stages_json=json.dumps(stage_inventory,ensure_ascii=False)
        run.limitations_json = json.dumps(list(dict.fromkeys(limitations)), ensure_ascii=False)
        run.completed_at = datetime.now(timezone.utc); run.progress_stage = "report" if run.status == "failed" else "done"
        audit_event(events, "report", "Research run finalized", {"status":run.status,"observations":len(observations),"hypotheses":len(hypotheses)})
        run.audit_log_json = json.dumps(events, ensure_ascii=False)
        db.commit(); db.refresh(run)
        return run
    except RunCancelled:
        db.rollback()
        run = db.get(ResearchRun, run_id)
        run.status = "cancelled"; run.completed_at = datetime.now(timezone.utc); run.progress_stage = "cancelled"
        run.summary = "Research run cancelled by the user. Partial sources, if any, are retained for inspection; no discovery was produced."
        run.limitations_json = json.dumps(list(dict.fromkeys(limitations + ["Run was cancelled before completion; results are incomplete."])))
        audit_event(events, "report", "Run cancelled by user", {})
        run.audit_log_json = json.dumps(events, ensure_ascii=False)
        db.commit(); db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        run = db.get(ResearchRun, run.id)
        if run:
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.summary = "Research run failed safely. Inspect the error summary and retry after resolving the issue."
            run.limitations_json = json.dumps(list(dict.fromkeys(limitations + [f"Unhandled research-stage failure: {type(exc).__name__}: {str(exc)[:220]}"])))
            run.audit_log_json = json.dumps(events, ensure_ascii=False)
            db.commit(); db.refresh(run)
            return run
        raise


# ---------------------------------------------------------------- background execution
_TASKS: set = set()

def launch_background(run_id: str, urls: list[str]) -> None:
    """Run a queued research run as an asyncio task with its own DB session. State lives in the DB,
    so progress survives page reloads; a process restart marks interrupted runs (see recover_interrupted_runs)."""
    from backend.app.db import SessionLocal
    async def _job():
        with SessionLocal() as session:
            try:
                await execute_run(session, run_id, urls)
            except Exception:
                session.rollback()
    task = asyncio.create_task(_job())
    _TASKS.add(task); task.add_done_callback(_TASKS.discard)

def recover_interrupted_runs(db: Session) -> int:
    """On startup, runs left queued/running by a previous process can never finish: mark them failed honestly."""
    stuck = db.query(ResearchRun).filter(ResearchRun.status.in_(["queued", "running"])).all()
    for r in stuck:
        r.status = "failed"; r.progress_stage = "interrupted"; r.completed_at = datetime.now(timezone.utc)
        r.summary = "Run was interrupted by a server restart before completion. Start a new run to retry."
        r.limitations_json = json.dumps(list(dict.fromkeys(json.loads(r.limitations_json or "[]") + ["Run interrupted by server restart."])))
    db.commit()
    return len(stuck)
