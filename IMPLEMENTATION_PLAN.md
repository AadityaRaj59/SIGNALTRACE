# SIGNALTRACE implementation status

## Implemented and locally tested in this repair
- [x] Project-root `.env` resolution independent of current working directory, `SIGNALTRACE_ENV_FILE` override, empty environment-variable handling, placeholder secret filtering, and root-anchored relative SQLite URLs.
- [x] Secret-free provider configuration-source diagnostics and optional `/api/diagnostics` gated by `EXPOSE_DIAGNOSTICS`.
- [x] APInex-compatible `OPENAI_BASE_URL`, configurable model, separate LLM timeout/retries, classified provider errors, bounded transient retries, JSON-mode compatibility fallback, strict JSON object parsing, and provider health status.
- [x] Tavily errors classified instead of silently returning an empty result; bounded transient retries, bearer header plus compatible request-body key, optional domain filtering, and health status.
- [x] Focused query planner for quarterly results, annual reports, advances/deposits, asset quality, profit quality, funding mix, exchange filings, regulatory data, and contradiction search.
- [x] Source relevance scoring with explicit reasons; generic homepages/navigation are not accepted as financial evidence; manual URLs pass the same relevance gate.
- [x] Source metadata now includes relevance, parse state, failure reason, independence group, scanned-PDF/OCR-required flags, and persisted stage inventory.
- [x] HTML main-content extraction, boilerplate removal, publication-date regex repair, pipe-delimited table rows preserving headers and periods, bounded PDF extraction, page markers, optional `pdfplumber` table recovery, and scanned-PDF detection.
- [x] Bounded concurrent source fetching via an `asyncio.Semaphore`, with structured fetch/parse/relevance failure reasons.
- [x] Deterministic structured-table extraction plus sentence heuristics; tolerant source excerpt validation; source IDs/URLs remain enforced; normalization preserves currency/unit conversion metadata.
- [x] Metric registry improvements, ambiguous bare GNPA/NNPA handling, AUM no longer aliased to advances, and corrected `lakh crore = 10^12` normalization.
- [x] Decimal-based trend calculations, adjacent-period QoQ/YoY comparisons, CAGR (three or more compatible periods), growth acceleration, derived credit-deposit/CASA ratios, and explicit skips for unknown units or incompatible bases.
- [x] Deterministic signal generation for advances/deposits divergence, asset-quality deterioration with loan growth, NIM compression, cost-to-income deterioration, CASA mix decline, and a limited profit-quality pattern when the needed inputs exist.
- [x] Explainable signal-confidence breakdown, evidence IDs, alternatives, missing evidence, counter-evidence review, and falsification conditions. LLMs do not invent the primary thesis.
- [x] Run statuses distinguish `needs_input`, `failed`, `abstained`, `completed_with_limitations`, and `completed`; technical failures are not silently relabeled as abstentions.
- [x] Additive schema changes preserve existing databases. Added APIs for run stages, observations, calculations, signals, and audit.
- [x] Shared report model now drives HTML and PDF exports with coverage, section-by-section data gaps, signals, timeline, formulas, source relevance/provenance, alternatives, confidence methodology, and limitations. PDF has page numbers and headers/footers.
- [x] Dashboard surfaces pipeline stages, source relevance/parse states, deterministic calculations, honest empty states, and new run statuses without production mock data.

## Verification performed
- `python -m compileall -q backend`: passed.
- `python -m pytest -q -p no:cacheprovider`: **56 passed**; two FastAPI `on_event` deprecation warnings remain.
- Extracted the dashboard inline JavaScript and ran `node --check`: passed.
- Automated end-to-end pipeline tests include a mocked HDFC-shaped acceptance fixture and a mocked Alpha Bank pipeline. These fixtures are test-only; no live HDFC Bank research run was performed in this environment, so no claim of live financial evidence is made.

## Still open / limitations
- Run a live HDFC Bank acceptance test on the user's Windows machine with the configured APInex and Tavily credentials. Verify actual search results, source relevance, parsed filings, validated observations, calculations, and exports.
- OCR is not implemented as an engine; scanned PDFs are flagged as requiring OCR. `pdfplumber` table extraction is optional and only runs if the package is installed.
- Some financial filings are image-based, dynamically rendered, blocked, or use tables whose units/basis are not explicit. Such observations must remain missing or flagged rather than guessed.
- Confidence scoring and deterministic signal thresholds are conservative engineering heuristics, not a validated investment model. Analyst review remains required.
- FastAPI startup uses the legacy `on_event` hook and emits a deprecation warning; it is not a functional test failure.
- Multi-user authentication, production deployment hardening, formal migrations/backups, and security review remain out of scope for this local repair.
