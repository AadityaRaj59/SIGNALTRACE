# Judging Criteria — Internal Checklist

| Dimension | Evidence to show | Current implementation / limitation |
|---|---|---|
| Generalization | Run the same code on unfamiliar Track A companies | Company input is runtime-defined; live multi-company validation still required |
| Source credibility | Open source links, publisher, dates, retrieval timestamps and excerpts | Implemented for fetched sources |
| Extraction provenance | Show the exact passage and source identity gate | Implemented; LLM semantic errors remain possible |
| Reproducible calculations | Formula, periods, units/basis and observation IDs | Deterministic helpers and ledger implemented |
| Non-obvious discovery | Hypothesis supported by multi-source quantitative evidence | Candidate engine implemented; every result requires analyst review |
| Contradiction search | Show targeted query, retrieved pages, extracted counter-observations | Bounded flow implemented; coverage depends on search/LLM keys and sources |
| Evidence independence | Show deduplication and source diversity | Exact URL/content hash dedupe; advanced syndicated clustering remains incomplete |
| Abstention | No forced discovery with weak evidence | Implemented |
| Auditability | Run ID, audit events, source records, limitations, exports | Implemented |
| Production reliability | Durable queue, auth, rate limits, migration/backups, egress isolation | Not yet production-grade |
