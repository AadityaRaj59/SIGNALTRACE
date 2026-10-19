# SIGNALTRACE UI transformation

## What changed

The dashboard is now organized as a financial-intelligence workspace while retaining the existing FastAPI research pipeline and single-page frontend. The redesign deliberately avoids a frontend framework migration and does not require npm.

- **Overview:** existing saved-run KPIs, run outcomes, evidence strength, activity and recent runs.
- **Analytics:** aggregate saved-run coverage, source counts, observations, calculations, strength distribution, status distribution and activity.
- **Signal Explorer:** searchable, filterable inventory of saved hypotheses and verified observations, each linked to its originating run.
- **Companies:** groups existing runs by company name and summarizes stored coverage.
- **Evidence Ledger:** searchable source registry with retrieval status, publisher, publication metadata and stored excerpts.
- **Research History:** existing run list and deletion workflow.
- **Reports:** HTML/PDF export links for saved runs.
- **Research detail:** existing hypotheses, observations, calculations, sources, audit trail, limitations, cancel, re-run and export features remain available.

## Integrity rules

The new views aggregate API/database records and do not add fabricated companies, signal values, returns, prices, performance rates or synthetic research outcomes. A retrieved source is not automatically corroboration. Evidence-strength labels remain research classifications, not investment ratings. Empty states explain when no records exist yet.

## Run locally (Windows PowerShell)

```powershell
cd C:\Users\ASUS\Desktop\SIGNALTRACE\SIGNALTRACE
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

Open `http://127.0.0.1:8000`. Existing `.env`, virtual environment and SQLite database should be preserved when applying this UI update to an existing checkout.

## Validation

The included backend test suite passes: `32 passed`. The inline JavaScript was checked with `node --check`.
