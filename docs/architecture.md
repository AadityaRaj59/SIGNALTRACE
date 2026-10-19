# Architecture

```mermaid
flowchart TD
  UI[Browser research dashboard] --> API[FastAPI]
  API --> DB[(SQLite)]
  API --> ORCH[Bounded research orchestrator]
  ORCH --> PLAN[Track A research plan]
  ORCH --> SEARCH[Optional Tavily source search]
  ORCH --> SAFE[URL validation + DNS checks + fetch limits]
  SAFE --> PARSE[HTML / text / PDF parser]
  PARSE --> SRC[(Source records + hashes + excerpts)]
  SRC --> EXTRACT[Optional LLM observation extractor]
  EXTRACT --> GATE[Exact excerpt / value / metric / period validation]
  GATE --> CALC[Deterministic decimal calculations]
  CALC --> HYP[Evidence-ID constrained hypotheses]
  HYP --> SKEPTIC[Targeted contradiction search]
  SKEPTIC --> EVAL[Counter-observation evaluation]
  EVAL --> REPORT[Structured report + audit log]
  REPORT --> UI
  REPORT --> HTML[HTML export]
  REPORT --> PDF[PDF export]
```

The current version is a modular monolith. Research runs are persisted, but processing still occurs in the API request lifecycle; a durable background queue is not implemented. Search and LLM integrations are optional and configured server-side. Without keys, the app remains runnable and abstains rather than fabricating a discovery.

## Evidence flow
1. Search results and manually supplied public URLs become candidate source URLs.
2. URL safety checks and bounded retrieval run before parsing.
3. Source metadata, content hash, full extracted text, excerpt and fetch errors are persisted.
4. The optional LLM extractor must return a passage that is an exact substring of source text. Code checks source ID/URL, raw value, metric label and period against that passage.
5. Only accepted observations enter calculation/hypothesis stages. Calculations are deterministic and period/reporting-basis aware.
6. Hypotheses must cite observation/calculation IDs. A skeptic query is run when search is configured. Candidate pages are fetched and their observations are separately validated before they may be marked as opposing observations.
7. Evidence scoring remains conservative. Search result presence alone is not corroboration; Strong is not assigned while the counter-evidence path is not fully resolved.

## Security boundary
The fetcher rejects unsafe URL schemes, embedded credentials, localhost/private/reserved targets and unsafe redirects, and enforces timeouts and response-size limits. DNS checks alone do not fully prevent DNS rebinding because the HTTP client may resolve the hostname separately. Production deployment must also enforce network-level egress restrictions.
