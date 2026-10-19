# SIGNALTRACE — Winter Coders | ATMOS 2026 | Track A

SIGNALTRACE is an evidence-first public-source financial research agent for banks, NBFCs, and financial-services companies. It is designed to investigate a company entered at runtime, preserve source provenance, calculate compatible financial changes deterministically, test candidate hypotheses, and abstain when evidence is insufficient.

## Implemented in this build
- FastAPI backend and responsive research dashboard
- SQLite persistence for research runs, source records, observations, calculations, hypotheses, limitations, and audit events
- Optional Tavily search for source discovery and targeted contradiction-search leads
- Optional OpenAI-compatible chat-completions adapter for structured financial-observation extraction and hypothesis generation
- Manual public URL fallback when search is not configured
- HTML and text parsing plus PDF text extraction with page labels
- Source excerpt verification: an extracted observation is accepted only when its quoted excerpt is an exact substring of retrieved text and its raw value appears in that excerpt
- Deterministic decimal-based percentage and basis-point calculation helpers, plus period/reporting-basis compatibility checks
- Candidate hypothesis generation constrained to stored observation/calculation IDs
- Contradiction-search query/result logging; search leads are not counted as corroboration until their claims are validated
- Evidence-strength explanation and conservative scoring (the current pipeline does not assign Strong while counter-search leads remain unevaluated)
- HTML and PDF report exports, run history, source explorer, observation ledger, calculation ledger, hypotheses, falsification conditions, limitations, and audit events
- URL scheme checks, credential-in-URL rejection, private/reserved IP rejection, redirect validation, response size limits, and timeouts
- Automated tests and GitHub Actions test workflow

## New in this update
- Full workspace redesign with a polished responsive shell and dedicated Overview, Analytics, Signal Explorer, Companies, Evidence Ledger, Research History, and Reports pages; new workspaces aggregate saved API records only and do not invent sample metrics.
- Redesigned dashboard: KPI overview, charts, live pipeline stepper, tabbed run view, clickable evidence IDs, trend charts, dark mode, mobile layout
- Background runs with live progress and cooperative cancel; interrupted runs are marked failed on restart
- Rule-based extractor that works with no API key (explicit "metric value period" sentences only, low confidence, same provenance gate)
- Metric-name normalisation and fiscal-period sorting; ratio metrics use basis-point change
- Independent-source clustering (same publisher domain or near-duplicate text) used in evidence scoring
- Optional bearer-token auth (`SIGNALTRACE_API_TOKEN`), rate limit (`RATE_LIMIT_PER_MINUTE`), run deletion, additive DB schema upgrade
- Fixed: PDF export crashed whenever a run had sources
- New endpoints: `POST /api/research-runs/{id}/cancel`, `DELETE /api/research-runs/{id}`, `GET /api/dashboard/stats`, `GET /api/stages`; `POST /api/research-runs` accepts `"background": true`

## Honest limitations
This is a strong hackathon implementation foundation, but not a substitute for financial analyst review. LLM extraction is fallible even when the cited excerpt is verified. It does not yet reliably normalize all bank-specific reporting definitions, extract all financial tables from complex PDFs, guarantee source independence beyond canonical URL/content-hash deduplication, or provide production-grade multi-user auth, durable background workers, rate limiting, or full deployment hardening. Treat findings as research hypotheses, not investment advice. Do not claim a Strong discovery unless the evidence and counter-evidence have actually been validated.

## Requirements
- Python 3.11+
- Optional `TAVILY_API_KEY` for web search
- Optional `OPENAI_API_KEY` for structured extraction and hypothesis generation
- No Node.js/npm required; the frontend is served by FastAPI

## Windows PowerShell setup
```powershell
cd SIGNALTRACE
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env to add TAVILY_API_KEY and OPENAI_API_KEY if available
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```
Open `http://127.0.0.1:8000`. API docs: `http://127.0.0.1:8000/docs`.

If using activation instead:
```powershell
.\.venv\Scripts\Activate.ps1
```
If PowerShell blocks activation, run commands with `\.venv\Scripts\python.exe` as shown above.

## macOS/Linux setup
```bash
cd SIGNALTRACE
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn backend.app.main:app --reload
```

## Environment variables
| Variable | Purpose | Default |
|---|---|---|
| `SIGNALTRACE_DATABASE_URL` | SQLAlchemy database URL | `sqlite:///./signaltrace.db` |
| `TAVILY_API_KEY` | Search and contradiction-search provider | unset |
| `OPENAI_API_KEY` | Structured observation extraction and hypothesis generation | unset |
| `OPENAI_MODEL` | Chat completions model | `gpt-4.1-mini` |
| `FRONTEND_ORIGINS` | Comma-separated allowed CORS origins | `http://localhost:8000,http://127.0.0.1:8000` |
| `REQUEST_TIMEOUT_SECONDS` | Network timeout | `15` |
| `MAX_SOURCE_BYTES` | Maximum downloaded response | `5000000` |
| `MAX_DOCUMENT_CHARS` | Maximum extracted text passed downstream | `120000` |
| `MAX_RESEARCH_SOURCES` | Maximum source URLs per run | `12` |
| `USER_AGENT` | Public retrieval user-agent | SignalTrace research agent |

Never commit `.env`; API keys are server-side. `.env.example` contains placeholders only.

## Run tests
```bash
python -m pytest -q
```
Tests use local fixtures and do not require API keys or live network access.

## API endpoints
- `GET /api/health` — status and configured capabilities
- `POST /api/research-runs` — start a research run
- `GET /api/research-runs` — history
- `GET /api/research-runs/{run_id}` — run status and summary
- `GET /api/research-runs/{run_id}/events` — audit/progress events
- `GET /api/research-runs/{run_id}/sources` — source provenance
- `GET /api/research-runs/{run_id}/report` — structured report
- `GET /api/research-runs/{run_id}/export.html` — HTML report export
- `GET /api/research-runs/{run_id}/export.pdf` — PDF report export

Example request:
```json
{
  "company": "Example Bank",
  "focus": "loan growth versus asset quality",
  "urls": ["https://example.com/investor-relations"]
}
```

## Research integrity rules
- No buy/sell/hold recommendations, target prices, or personalized investment advice.
- No fabricated URLs, values, publication dates, source passages, or conclusions.
- Reported facts, computed results, and hypotheses must remain distinguishable.
- Missing values are never converted to zero.
- A fetched page is not proof of an observation; an observation is not proof of a hypothesis.
- Search results for a contradiction query are leads, not automatically opposing evidence.
- If data is weak or contradictory, abstain and explain the coverage gaps.

## Security and deployment
The fetcher restricts URL schemes, rejects credentials in URLs and private/reserved/loopback addresses, validates redirect targets, limits response sizes, and uses request timeouts. DNS checks cannot completely eliminate DNS-rebinding risks when the HTTP client resolves the host separately; production deployment should also enforce network-level egress restrictions. Before public deployment, add authentication/authorization, rate limiting, durable background jobs, database migrations/backups, observability, and a formal security review. The current app is a single-user hackathon demo. Runs can execute synchronously in the API request lifecycle or in an in-process background task; background tasks are not durable across process loss.

## Demo workflow
1. Enter a Track A company not hardcoded into the app.
2. Inspect the research plan and retrieved source dates.
3. Open the source and verify extracted excerpts.
4. Inspect calculations and their input observation IDs.
5. Review the leading candidate hypothesis, alternatives, and falsification condition.
6. Inspect contradiction-search queries/results and their limitations.
7. Export the HTML report.
8. Demonstrate graceful failure with missing credentials or a rejected localhost URL.
9. Explain the limitations honestly. A defensible abstention is preferable to a fabricated discovery.

## Provider configuration and diagnostics (updated)

SIGNALTRACE resolves `.env` from the repository root, independent of the PowerShell current directory. Set `SIGNALTRACE_ENV_FILE` before starting the server only if you intentionally use a different environment file. Empty environment variables are ignored so an exported empty `TAVILY_API_KEY` does not mask the `.env` value. Relative SQLite URLs are anchored to the project root.

For APInex, configure the OpenAI-compatible client with:

```env
OPENAI_API_KEY=your_apinex_api_key
OPENAI_BASE_URL=https://api.apinex.bond/v1
OPENAI_MODEL=free/gemini-3.8-flash
LLM_TIMEOUT_SECONDS=60
LLM_JSON_MODE=auto
TAVILY_API_KEY=your_tavily_api_key
```

Keep real keys only in `.env`; do not paste them into the dashboard, source files, reports, or chat. `gpt-6-luna` is not the default because the current account returned HTTP 402 for insufficient balance. A configured provider is not necessarily reachable; `/api/health?live=true` requests live provider checks. `/api/diagnostics` is disabled by default; set `EXPOSE_DIAGNOSTICS=true` only on a trusted local deployment to enable sanitized configuration-source diagnostics. Live diagnostics may consume provider usage.

The research pipeline now demotes generic homepages, preserves HTML table rows with their period headers, uses deterministic table/sentence extraction and calculation-driven signal rules, and records source relevance, parsing state, failure reason, and stage outcomes. A report may legitimately abstain or complete with limitations; the system must not invent figures to fill missing sections.

### Windows PowerShell

```powershell
cd "$env:USERPROFILE\Desktop\SIGNALTRACE_FIXED\SIGNALTRACE"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The live HDFC Bank acceptance run must be performed locally with your own configured keys; automated tests use mocked sources and do not claim live financial evidence.
