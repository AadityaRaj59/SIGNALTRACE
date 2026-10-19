import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from backend.app.services.calculations import percentage_change, basis_point_change, CalculationError
from backend.app.services.metrics import normalize_metric, is_ratio_metric, period_sort_key
from backend.app.services.llm_provider import json_completion, LLMUnavailable
from backend.app.services.search_provider import search_public_sources
from backend.app.services.metrics import normalize_metric

PLANNER_ANGLES = [
    {"angle":"Loan growth versus asset quality", "metrics":["advances", "gross NPA", "net NPA", "slippages", "credit cost", "provision coverage"], "test":"Compare at least two dated and basis-compatible periods; check portfolio mix and write-offs."},
    {"angle":"Funding mix and dependency", "metrics":["deposits", "borrowings", "certificates of deposit", "funding partners"], "test":"Look for disclosed funding composition and concentration across periods; do not infer dependency from a single mention."},
    {"angle":"Profit quality", "metrics":["PAT", "NII", "NIM", "other income", "provisions", "operating costs"], "test":"Compare profit changes with core income, costs, provisions and one-offs where comparable data exists."},
    {"angle":"Temporal / narrative divergence", "metrics":["dated filings", "management commentary", "public reporting"], "test":"Use publication timestamps and reporting periods; do not infer a narrative lead without dated evidence."},
]

async def build_hypotheses(company: str, focus: str | None, observations: list[dict], calculations: list[dict]) -> tuple[list[dict], list[str]]:
    """Build candidate signals deterministically from validated calculations; never ask an LLM to invent a thesis."""
    limitations=[]
    by_metric=defaultdict(list)
    for calc in calculations:
        by_metric[str(calc.get("metric_name","")).strip().lower()].append(calc)
    obs_by_id={o.get("id"):o for o in observations}
    candidates=[]
    def value(calc):
        try: return Decimal(str(calc.get("result_percent",calc.get("result_basis_points","0"))))
        except (InvalidOperation,TypeError): return Decimal("0")
    def add(statement, rationale, calcs, obs_ids, alternatives, missing, falsification):
        if not calcs or len(set(x for x in obs_ids if x))<2: return
        candidates.append({"id":f"signal_rule_{len(candidates)+1}","statement":statement,"rationale":rationale,
            "supporting_observation_ids":list(dict.fromkeys(x for x in obs_ids if x)),"opposing_observation_ids":[],
            "calculation_ids":list(dict.fromkeys(c.get("id") for c in calcs if c.get("id"))),"alternatives":alternatives,
            "missing_information":missing,"falsification_condition":falsification,"confidence":0.35,
            "generation_method":"deterministic_calculation_rule","status":"candidate","why_it_matters":rationale})
    advances=by_metric.get("advances",[])+by_metric.get("total advances",[])
    deposits=by_metric.get("deposits",[])+by_metric.get("total deposits",[])
    def last_calc(items): return items[-1] if items else None
    a=last_calc(advances); d=last_calc(deposits)
    if a and d and a.get("start_period")==d.get("start_period") and a.get("end_period")==d.get("end_period"):
        av=value(a); dv=value(d)
        if av-dv >= Decimal("3"):
            ids=a.get("input_observation_ids",[])+d.get("input_observation_ids",[])
            add(f"Advances growth exceeded deposit growth by {(av-dv).quantize(Decimal('0.01'))} percentage points over {a.get('start_period')} to {a.get('end_period')}.",
                "Loan growth is outpacing deposit growth over the same comparison window; funding implications require review of wholesale funding, liquidity and portfolio mix.",[a,d],ids,
                ["Different period definitions or reporting bases could make the comparison invalid.","Changes in deposit mix or liquidity buffers may mitigate the apparent gap."],
                ["Comparable source coverage for borrowings, liquidity and deposit composition."],
                "Reassess if comparable advances/deposit growth or funding disclosures remove the growth gap.")
    npa_calcs=[c for k,items in by_metric.items() if "npa" in k and "ratio" in k for c in items]
    npa_increases=[c for c in npa_calcs if c.get("result_basis_points") is not None and value(c)>0]
    if a and npa_increases:
        n=npa_increases[-1]
        ids=a.get("input_observation_ids",[])+n.get("input_observation_ids",[])
        add(f"Loan growth coincided with a rise in {n.get('metric_name')} of {n.get('result_basis_points')} basis points.",
            "Growth and deterioration in an asset-quality indicator appear in the supplied comparable periods; causality is not established.",[a,n],ids,
            ["Portfolio mix, write-offs, recoveries, reclassification or macro conditions may explain the NPA movement."],
            ["Slippage, credit-cost, write-off and portfolio-vintage data."],
            "Reassess if like-for-like NPA measures are stable or declining, or if the apparent increase is due to incompatible definitions.")
    nim_calcs=[c for k,items in by_metric.items() if "margin" in k or k=="net interest margin" for c in items]
    pat_calcs=by_metric.get("profit after tax",[])+by_metric.get("pat",[])
    if nim_calcs and any(c.get("result_basis_points") is not None and value(c)<0 for c in nim_calcs):
        n=next(c for c in reversed(nim_calcs) if c.get("result_basis_points") is not None and value(c)<0)
        ids=n.get("input_observation_ids",[])
        add(f"Net interest margin compressed by {abs(value(n))} basis points from {n.get('start_period')} to {n.get('end_period')}.",
            "Margin compression can signal pressure on core intermediation economics; it should be assessed alongside funding costs, asset yields and mix.",[n],ids,
            ["Seasonality, balance-sheet mix and changes in low-yield liquidity assets may explain the movement."],
            ["Comparable cost of funds, yield on advances and average balance-sheet data."],
            "Reassess if a comparable NIM series is stable or increasing after consistent basis validation.")
    cti=[c for k,items in by_metric.items() if "cost-to-income" in k or "cost to income" in k for c in items]
    if cti and value(cti[-1])>0:
        c=cti[-1]; add(f"Cost-to-income ratio increased by {c.get('result_basis_points',c.get('result_percent'))} over {c.get('start_period')} to {c.get('end_period')}.",
            "A rising cost-to-income ratio may indicate operating leverage pressure, subject to consistent ratio definitions.",[c],c.get("input_observation_ids",[]),
            ["One-off technology, integration or branch expansion costs may temporarily raise expenses."],["Expense breakdown and one-off cost disclosures."],
            "Reassess if comparable expense and income data show the increase was temporary or definition-driven.")
    casa=[c for k,items in by_metric.items() if "casa ratio" in k for c in items]
    if casa and value(casa[-1])<0:
        c=casa[-1]; add(f"CASA ratio declined by {abs(value(c))} basis points between {c.get('start_period')} and {c.get('end_period')}.",
            "A lower CASA ratio may indicate a shift toward relatively more expensive deposit funding; funding-cost disclosures are needed to assess impact.",[c],c.get("input_observation_ids",[]),
            ["Deposit mix may be seasonal or reflect a deliberate customer/acquisition strategy."],["Deposit pricing, term-deposit mix and cost-of-funds data."],
            "Reassess if comparable CASA data rebounds or the apparent change reflects a changed definition.")
    pat=last_calc(by_metric.get("profit after tax",[])+by_metric.get("pat",[]))
    nii=last_calc(by_metric.get("net interest income",[])+by_metric.get("nii",[]))
    other=last_calc(by_metric.get("other income",[]))
    if pat and nii and other and pat.get("start_period")==nii.get("start_period")==other.get("start_period") and pat.get("end_period")==nii.get("end_period")==other.get("end_period"):
        if value(pat)>value(nii) and value(other)>value(nii):
            ids=pat.get("input_observation_ids",[])+nii.get("input_observation_ids",[])+other.get("input_observation_ids",[])
            add("PAT growth materially outpaced net interest income growth while other income also grew over the same comparison window.",
                "This pattern warrants checking whether profit growth is supported by core interest income or more volatile non-interest sources; it does not by itself prove weak earnings quality.",[pat,nii,other],ids,
                ["Changes in taxes, provisions, exceptional items or mix may explain the difference."],["A full profit bridge, one-off items, tax and provision disclosures."],
                "Reassess when a comparable profit bridge shows core income explains the PAT growth.")
    if not candidates:
        limitations.append("No deterministic signal rule met its minimum comparable-period and evidence requirements; no thesis was invented.")
    return candidates[:8],limitations

async def challenge_hypotheses(hypotheses: list[dict], company: str) -> tuple[list[dict], list[str]]:
    limitations = []
    for h in hypotheses:
        query = f'"{company}" {h.get("statement", "")} risk explanation contrary evidence accounting change acquisition portfolio mix'
        h["counter_search_queries"] = [query]
        h["counter_search_results"] = []
        if not query.strip() or len(query) < 15:
            continue
        try:
            results = await search_public_sources(query, limit=5)
            h["counter_search_results"] = results
            if not results:
                h["counter_search_coverage"] = "No search results returned; this is not evidence against the hypothesis."
            else:
                h["counter_search_coverage"] = f"Searched one targeted contradiction query and retrieved {len(results)} result(s); these results require parsing and claim-level evaluation before being counted as opposing evidence."
        except Exception as exc:
            h["counter_search_coverage"] = f"Counter-search failed: {type(exc).__name__}: {str(exc)[:120]}"
            limitations.append(h["counter_search_coverage"])
    if hypotheses and not get_search_configured():
        limitations.append("Contradiction search is not configured because TAVILY_API_KEY is missing; no counter-evidence search is claimed.")
    return hypotheses, limitations

def get_search_configured() -> bool:
    from backend.app.core.config import get_settings
    return bool(get_settings().tavily_api_key)

def calculate_observation_trends(observations: list[dict]) -> tuple[list[dict], list[str]]:
    """Calculate comparable series changes with explicit input IDs and skip reasons."""
    import re
    grouped=defaultdict(list); limitations=[]
    for obs in observations:
        if obs.get("validation_status")!="source_excerpt_verified": continue
        raw=str(obs.get("raw_value","")).strip().replace("−","-")
        if not re.fullmatch(r"\(?\s*[-+]?(?:[\d,]+)(?:\.\d+)?%?\s*\)?",raw):
            obs.setdefault("validation_flags",[]).append("Raw value is not a strict numeric token; no calculation performed."); continue
        try:
            value=Decimal(raw.replace(",","").replace("%","").replace("(","-").replace(")", ""))
        except (InvalidOperation,ValueError):
            obs.setdefault("validation_flags",[]).append("Value not safely numeric; no calculation performed."); continue
        canon=obs.get("metric_canonical") or normalize_metric(obs["metric_name"],obs.get("unit","")); obs["metric_canonical"]=canon
        unit_value=obs.get("unit","").strip().lower()
        if unit_value in {"unknown","","unspecified"} and not is_ratio_metric(canon,unit_value):
            limitations.append(f"Skipped calculation for {canon} in {obs.get('period')}: unit is unknown; no magnitude was assumed.")
            continue
        key=(canon.strip().lower(),unit_value,obs.get("reporting_basis","unknown").strip().lower(),obs.get("period_type") or ("ratio" if is_ratio_metric(canon,unit_value) else "level"))
        grouped[key].append((obs,value))
    calculations=[]
    for (metric,unit,basis,period_type),items in grouped.items():
        sortable=[(o,v,period_sort_key(o.get("period",""))) for o,v in items]
        sortable=[x for x in sortable if x[2] is not None]
        sortable.sort(key=lambda x:(x[2],x[0].get("period_end") or "",x[0].get("publication_date") or ""))
        seen_period={}; dedup=[]
        for x in sortable:
            p=x[2]
            if p not in seen_period:
                seen_period[p]=x; dedup.append(x)
            elif seen_period[p][1] != x[1]:
                limitations.append(f"Conflicting values for {metric} in period {x[0].get('period')} across sources; duplicate period was not treated as an additional trend point.")
        sortable=dedup
        if len(sortable)<2: continue
        first,last=sortable[0],sortable[-1]
        try:
            if is_ratio_metric(first[0].get("metric_canonical",metric),unit):
                calc=basis_point_change(first[1],last[1],start_period=first[0]["period"],end_period=last[0]["period"],start_basis=basis,end_basis=last[0].get("reporting_basis",basis))
                calc["kind"]="basis_points"; calc["unit"]="bps"
            else:
                calc=percentage_change(first[1],last[1],unit=unit or "unit unspecified",start_period=first[0]["period"],end_period=last[0]["period"],start_basis=basis,end_basis=last[0].get("reporting_basis",basis))
                calc["kind"]="percent"
            p0,p1=first[2],last[2]
            calc["period_comparison"]="period_over_period"
            if p0[1] and p1[1] and p0[1]==p1[1] and p1[0]-p0[0]==1: calc["period_comparison"]="yoy"
            elif p0[1] and p1[1] and (p1[0]*4+p1[1])-(p0[0]*4+p0[1])==1: calc["period_comparison"]="qoq"
            calc["series"]=[{"period":o["period"],"value":str(v),"observation_id":o.get("id"),"period_start":o.get("period_start"),"period_end":o.get("period_end")} for o,v,_ in sortable]
            calc.update({"id":f"calc_{len(calculations)+1}","metric_name":first[0].get("metric_canonical",metric),"formula":calc.get("formula"),
                "input_observation_ids":[first[0].get("id"),last[0].get("id")],"source_ids":[first[0].get("source_id"),last[0].get("source_id")],
                "normalized_inputs":[str(first[1]),str(last[1])],"rounding_rule":"2 decimal places, half-even (Decimal default)",
                "validation_status":"deterministic","interpretation_limits":["Descriptive comparison only; not causal evidence.","Only same metric/unit/reporting basis group compared."],
                "start_period":first[0]["period"],"end_period":last[0]["period"]})
            calculations.append(calc)
            # Add adjacent-period comparisons so quarterly/annual YoY and QoQ changes are not hidden by a first-to-last aggregate.
            if len(sortable)>2:
                for pair_a,pair_b in zip(sortable,sortable[1:]):
                    try:
                        if is_ratio_metric(pair_a[0].get("metric_canonical",metric),unit):
                            pair_calc=basis_point_change(pair_a[1],pair_b[1],start_period=pair_a[0]["period"],end_period=pair_b[0]["period"],start_basis=basis,end_basis=pair_b[0].get("reporting_basis",basis))
                            pair_calc["kind"]="basis_points"; pair_calc["unit"]="bps"
                        else:
                            pair_calc=percentage_change(pair_a[1],pair_b[1],unit=unit or "unit unspecified",start_period=pair_a[0]["period"],end_period=pair_b[0]["period"],start_basis=basis,end_basis=pair_b[0].get("reporting_basis",basis))
                            pair_calc["kind"]="percent"
                        ka,kb=pair_a[2],pair_b[2]; comparison="period_over_period"
                        if period_type=="annual" and kb[0]-ka[0]==1: comparison="yoy"
                        elif ka[1] and kb[1] and ka[1]==kb[1] and kb[0]-ka[0]==1: comparison="yoy"
                        elif ka[1] and kb[1] and (kb[0]*4+kb[1])-(ka[0]*4+ka[1])==1: comparison="qoq"
                        pair_calc.update({"id":f"calc_{len(calculations)+1}","metric_name":pair_a[0].get("metric_canonical",metric),"period_comparison":comparison,
                            "formula":pair_calc.get("formula"),"input_observation_ids":[pair_a[0].get("id"),pair_b[0].get("id")],"source_ids":[pair_a[0].get("source_id"),pair_b[0].get("source_id")],
                            "normalized_inputs":[str(pair_a[1]),str(pair_b[1])],"series":[{"period":pair_a[0]["period"],"value":str(pair_a[1]),"observation_id":pair_a[0].get("id")},{"period":pair_b[0]["period"],"value":str(pair_b[1]),"observation_id":pair_b[0].get("id")}],
                            "rounding_rule":"2 decimal places","validation_status":"deterministic","interpretation_limits":["Adjacent-period comparison; not causal evidence."]})
                        calculations.append(pair_calc)
                    except CalculationError as exc:
                        limitations.append(f"Skipped adjacent-period calculation for {metric}: {exc}")
            # CAGR requires at least three compatible periods and positive endpoints.
            if len(sortable)>=3 and not is_ratio_metric(first[0].get("metric_canonical",metric),unit) and first[1]>0 and last[1]>0:
                years=Decimal(str(max(0,last[2][0]-first[2][0])))
                if years>0:
                    try:
                        cagr=(((last[1]/first[1]) ** (Decimal("1")/years))-Decimal("1"))*Decimal("100")
                        calculations.append({"id":f"calc_{len(calculations)+1}","name":"Compound annual growth rate","metric_name":first[0].get("metric_canonical",metric),"kind":"cagr","period_comparison":"cagr","formula":"((end / start) ^ (1 / elapsed_years) - 1) * 100","result_percent":str(cagr.quantize(Decimal("0.01"))),"unit":"%","start_period":first[0]["period"],"end_period":last[0]["period"],"input_observation_ids":[first[0].get("id"),last[0].get("id")],"source_ids":[first[0].get("source_id"),last[0].get("source_id")],"normalized_inputs":[str(first[1]),str(last[1])],"series":[{"period":o["period"],"value":str(v),"observation_id":o.get("id")} for o,v,_ in sortable],"rounding_rule":"2 decimal places","validation_status":"deterministic","interpretation_limits":["Requires at least three compatible observations.","Annualized rate is descriptive and not a forecast."]})
                    except (InvalidOperation,ZeroDivisionError):
                        limitations.append(f"Skipped CAGR for {metric}: endpoints could not be safely annualized.")
        except CalculationError as exc: limitations.append(f"Skipped calculation for {metric}: {exc}")
    # Growth acceleration is the change between two adjacent YoY/QoQ growth rates, not a forecast.
    growth_groups=defaultdict(list)
    for calc in calculations:
        if calc.get("kind")=="percent" and calc.get("period_comparison") in {"yoy","qoq"} and calc.get("result_percent") is not None:
            growth_groups[(calc.get("metric_name"),calc.get("period_comparison"))].append(calc)
    for (metric,comparison),items in growth_groups.items():
        if len(items)<2: continue
        items.sort(key=lambda c:period_sort_key(c.get("end_period","")) or (0,0))
        previous,current=items[-2],items[-1]
        try:
            delta=(Decimal(str(current["result_percent"]))-Decimal(str(previous["result_percent"]))).quantize(Decimal("0.01"))
        except (InvalidOperation,KeyError): continue
        calculations.append({"id":f"calc_{len(calculations)+1}","name":"Growth acceleration","metric_name":metric,"kind":"growth_acceleration","period_comparison":comparison,"formula":"latest comparable growth rate - previous comparable growth rate","result_percent":str(delta),"unit":"percentage points","start_period":previous.get("start_period"),"end_period":current.get("end_period"),"input_observation_ids":list(dict.fromkeys((previous.get("input_observation_ids") or [])+(current.get("input_observation_ids") or [])),),"input_calculation_ids":[previous.get("id"),current.get("id")],"source_ids":list(dict.fromkeys((previous.get("source_ids") or [])+(current.get("source_ids") or []))),"normalized_inputs":[previous.get("result_percent"),current.get("result_percent")],"series":[],"rounding_rule":"2 decimal places","validation_status":"deterministic","interpretation_limits":["Acceleration describes change in observed growth rates; it is not a forecast."]})
    # Derived ratios use only same-period, same-basis observations with matching explicit units.
    period_groups=defaultdict(dict)
    for obs in observations:
        if obs.get("validation_status")!="source_excerpt_verified": continue
        metric=obs.get("metric_canonical") or normalize_metric(obs.get("metric_name",""),obs.get("unit",""))
        key=(obs.get("period"),obs.get("reporting_basis","unknown"),obs.get("unit",""))
        try: v=Decimal(str(obs.get("raw_value","")).replace(",","").replace("%",""))
        except (InvalidOperation,ValueError): continue
        period_groups[key].setdefault(metric,[]).append((obs,v))
    for (period,basis,unit),metrics in period_groups.items():
        for numerator,denominator,display,formula in [("Advances","Deposits","Derived credit-deposit ratio","(total advances / total deposits) * 100"),("CASA deposits","Deposits","Derived CASA ratio","(CASA deposits / total deposits) * 100")]:
            if numerator not in metrics or denominator not in metrics: continue
            a=metrics[numerator][0]; d=metrics[denominator][0]
            if a[0].get("period_type")!=d[0].get("period_type") or a[1]<0 or d[1]<=0:
                limitations.append(f"Skipped {display} for {period}: inputs are incompatible or denominator is not positive."); continue
            result=(a[1]/d[1]*Decimal("100")).quantize(Decimal("0.01"))
            calculations.append({"id":f"calc_{len(calculations)+1}","name":display,"metric_name":display,"kind":"derived_ratio","formula":formula,"result_percent":str(result),"unit":"%","start_period":period,"end_period":period,"input_observation_ids":[a[0].get("id"),d[0].get("id")],"source_ids":[a[0].get("source_id"),d[0].get("source_id")],"normalized_inputs":[str(a[1]),str(d[1])],"series":[],"rounding_rule":"2 decimal places","validation_status":"deterministic","interpretation_limits":["Derived from same-period observations with matching unit and reporting basis.","Definitions should be checked against the company's reported ratio."]})
    return calculations,limitations

async def score_hypothesis(h: dict, observations: list[dict], calculations: list[dict], source_clusters: dict | None = None) -> dict:
    obs_by_id={o.get("id"):o for o in observations}; calc_by_id={c.get("id"):c for c in calculations}
    supports=[obs_by_id[x] for x in h.get("supporting_observation_ids",[]) if x in obs_by_id]
    opposing=[obs_by_id[x] for x in h.get("opposing_observation_ids",[]) if x in obs_by_id]
    valid_calcs=[calc_by_id[x] for x in h.get("calculation_ids",[]) if x in calc_by_id]
    source_ids={o.get("source_id") for o in supports if o.get("source_id") is not None}
    if source_clusters: source_ids={source_clusters.get(sid,{}).get("cluster",f"s{sid}") for sid in source_ids}
    relevance=[float(o.get("source_relevance_score")) for o in supports if o.get("source_relevance_score") is not None]
    extraction=[float(o.get("extraction_confidence",0)) for o in supports]
    confidence_breakdown={
        "provenance":round(sum(1 for o in supports if o.get("validation_status")=="source_excerpt_verified")/len(supports),2) if supports else 0.0,
        "source_quality":round(min(1.0,(sum(relevance)/len(relevance))/8),2) if relevance else 0.5,
        "independent_corroboration":round(min(1.0,len(source_ids)/3),2),
        "period_compatibility":1.0 if valid_calcs and all(c.get("start_period") and c.get("end_period") for c in valid_calcs) else 0.25,
        "extraction_confidence":round(sum(extraction)/len(extraction),2) if extraction else 0.0,
        "calculation_validity":1.0 if valid_calcs and all(c.get("validation_status")=="deterministic" for c in valid_calcs) else 0.0,
        "contradiction_review":1.0 if h.get("counter_evidence_assessment") in {"mixed","not_relevant","opposes"} else 0.35,
    }
    confidence=round(sum(confidence_breakdown.values())/len(confidence_breakdown),2)
    dimensions={"supporting_observations":len(supports),"distinct_supporting_sources":len(source_ids),"independence_clustered":bool(source_clusters),"opposing_observations":len(opposing),"has_deterministic_calculation":bool(valid_calcs),"counter_search_results":len(h.get("counter_search_results",[])),"confidence_breakdown":confidence_breakdown,"confidence_score":confidence}
    score=(1 if len(supports)>=2 else 0)+(1 if len(source_ids)>=2 else 0)+(1 if valid_calcs else 0)+(1 if h.get("falsification_condition") else 0)-(1 if opposing else 0)
    counter_reviewed=h.get("counter_evidence_assessment") in {"mixed","not_relevant"}
    if score>=4 and len(source_ids)>=3 and len(valid_calcs)>=2 and counter_reviewed and not opposing and confidence>=0.8: strength="Strong"
    elif score>=3 and len(source_ids)>=2 and counter_reviewed and not opposing and confidence>=0.65: strength="Moderate"
    else: strength="Weak"
    h["evidence_strength"]=strength; h["confidence_score"]=confidence; h["confidence_breakdown"]=confidence_breakdown; h["evidence_dimensions"]=dimensions
    h["score_explanation"]=f"{len(supports)} supporting observation(s), {len(source_ids)} independent source cluster(s), {len(opposing)} opposing observation(s), {len(valid_calcs)} validated deterministic calculation(s), confidence {confidence:.2f}. Search-result presence alone is not corroboration."
    return h

async def evaluate_counter_evidence(hypotheses: list[dict], observations: list[dict], counter_source_ids: set[int]) -> tuple[list[dict], list[str]]:
    """Evaluate extracted observations from counter-search pages against candidate claims.

    Only observation IDs from fetched contradiction-search sources may be returned as opposing IDs.
    This remains a model-assisted judgment and is surfaced with its rationale and source provenance.
    """
    limitations = []
    if not hypotheses or not counter_source_ids:
        return hypotheses, limitations
    counter_obs = [o for o in observations if o.get("source_id") in counter_source_ids]
    if not counter_obs:
        limitations.append("Contradiction pages were fetched, but no source-excerpt-verified financial observations were extracted from them.")
        return hypotheses, limitations
    obs_by_id = {o.get("id"): o for o in observations}
    for hypothesis in hypotheses:
        try:
            result = await json_completion(
                "You are a skeptical evidence evaluator. Return JSON with fields opposing_observation_ids (array of IDs chosen only from the provided counter_observations), assessment (one of 'opposes', 'mixed', 'not_relevant'), rationale (short), and unresolved_questions (array). A counter-observation opposes the hypothesis only if its exact metric, period, unit, reporting basis, and meaning materially contradict the claim. Similar topic is not contradiction. Do not infer values or facts.",
                {"hypothesis": {"statement": hypothesis.get("statement"), "rationale": hypothesis.get("rationale"), "supporting_observation_ids": hypothesis.get("supporting_observation_ids", [])},
                 "supporting_observations": [obs_by_id[x] for x in hypothesis.get("supporting_observation_ids", []) if x in obs_by_id],
                 "counter_observations": counter_obs}, max_tokens=1200)
            allowed = {o.get("id") for o in counter_obs}
            opposing = [x for x in result.get("opposing_observation_ids", []) if x in allowed]
            assessment = result.get("assessment", "mixed")
            if assessment not in {"opposes", "mixed", "not_relevant"}:
                assessment = "mixed"
            hypothesis["opposing_observation_ids"] = list(dict.fromkeys(hypothesis.get("opposing_observation_ids", []) + opposing))
            hypothesis["counter_evidence_assessment"] = assessment
            hypothesis["counter_evidence_rationale"] = str(result.get("rationale", "No rationale supplied."))[:600]
            hypothesis["unresolved_questions"] = [str(x)[:240] for x in result.get("unresolved_questions", [])[:5]]
        except Exception as exc:
            limitations.append(f"Counter-evidence evaluation failed: {type(exc).__name__}: {str(exc)[:150]}")
    return hypotheses, limitations
