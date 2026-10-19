# Demo Runbook

1. Start `uvicorn backend.app.main:app --reload`; open `http://127.0.0.1:8000`.
2. Check `/api/health` to show which optional integrations are configured.
3. Enter an unfamiliar Track A company and a focused research question.
4. Provide public investor-relations/filing URLs, or configure `TAVILY_API_KEY` for search.
5. Configure `OPENAI_API_KEY` for structured observation extraction and hypothesis generation.
6. Inspect source status, publication date when available, retrieval timestamp, exact excerpt, and errors.
7. Inspect accepted observations, deterministic calculations, hypothesis evidence IDs, alternatives, counter-search coverage, and falsification condition.
8. Open both HTML and PDF exports.
9. Demonstrate safe rejection of `http://127.0.0.1/` as a source URL.
10. Demonstrate graceful abstention when keys or validated observations are absent. Do not present a prior run as live or call a candidate a confirmed discovery without reviewing the evidence.
