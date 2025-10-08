# Holosun Dealer Locator Data Pipeline

Automation toolkit for enumerating Holosun dealers across California ZIP codes, normalizing the responses, and emitting a production-ready CSV with supporting metrics and audit artifacts.

## Prerequisites
- Python 3.11 or newer (development performed on macOS).
- `pip` for dependency management. Required runtime packages today: `requests` for HTTP operations and `pytest` for the test suite.
- Optional: `virtualenv` or `pyenv` for isolated environments.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install requests pytest
```

## Repository Layout
- `docs/` — design notes, context brief, and release documentation.
- `scripts/` — CLI entry points (`fetch_ca_zip_codes.py`, `fetch_single_zip.py`, `orchestrate_zip_runs.py`, exporters).
- `src/holosun_locator/` — reusable helpers (export utilities, normalization logic placeholders).
- `data/` — local storage for raw captures, processed tables, and orchestrator runs (ignored by git aside from placeholders).
- `logs/` — runtime logs, including `manual_attention.log` for ZIPs that require manual follow-up.
- `tests/` — automated pytest coverage.

## Core Workflow
1. **Fetch ZIP centroid table**
   ```bash
   python scripts/fetch_ca_zip_codes.py --output data/processed/ca_zip_codes.csv
   ```
   This script downloads and normalizes the California ZIP list (including latitude/longitude centroids) used by downstream jobs. Artifacts land in `data/processed/` with accompanying metadata.

2. **Dry-run a single ZIP (optional smoke check)**
   ```bash
   python scripts/fetch_single_zip.py 90001 --verbose
   ```
   Confirms request payload shape, anti-automation detection, and artifact layout before scheduling a full batch.

3. **Run the orchestrator over the ZIP corpus**
   ```bash
   python scripts/orchestrate_zip_runs.py \
       --zip-csv data/processed/ca_zip_codes.csv \
       --flush-every 25 \
       --deliverable-name holosun_ca_dealers.csv
   ```
   Outputs land under `data/raw/orchestrator_runs/<run_id>/`:
   - `normalized_dealers.json` and `normalized_dealers.csv`
   - Trimmed deliverable CSV (defaults to `holosun_ca_dealers.csv`)
   - Metrics JSON snapshot (same stem as deliverable)
   - `run_state.json` + `run_summary.json` describing progress, errors, and flush timestamps
   - Raw request/response payloads per ZIP when `--skip-raw` is not supplied

4. **Re-export or validate deliverables**
   ```bash
   python scripts/export_normalized_dealers.py \
       --input data/raw/orchestrator_runs/<run_id>/normalized_dealers.json \
       --output data/raw/orchestrator_runs/<run_id>/normalized_dealers.csv \
       --metrics-json data/raw/orchestrator_runs/<run_id>/normalized_dealers.metrics.json
   ```
   Useful when reformatting or when a downstream consumer needs metrics separate from the orchestrator run.

## Resume & Replay Tooling
- `--resume-state PATH` — point the orchestrator at a previous `run_state.json` or run directory. Existing dealers load into memory, and deliverables refresh in-place.
- `--resume-policy {skip,blocked,all}` —
  - `skip` (default) removes ZIPs that already completed in the source state.
  - `blocked` runs only ZIPs recorded as blocked (optionally augmented by the manual log).
  - `all` preserves the requested ZIP list (no filtering).
- `--include-manual-log` — merge ZIPs recorded in `logs/manual_attention.log`; pair with `--manual-log-run RUN_ID` to scope to a prior run (defaults to the resume run id when available).

Blocked ZIPs generate artifacts under `<run_dir>/blocked_zips/` and append JSON lines to `logs/manual_attention.log`. Review the log before replaying to ensure anti-automation mitigation or manual intervention is in place.

## Observability & Output
- Logging uses the `holosun.*` namespace. Pass `--verbose` to surface debug traces.
- Each batch flush recomputes deliverable CSV + metrics, ensuring resumable runs yield consistent artifacts even if interrupted.
- Metrics include counts for total/unique dealers, phone/email coverage, and source ZIP fan-out; they live beside the deliverable.
- Manual follow-ups are tracked in `logs/manual_attention.log`; use `jq`/`rg` or the resume tooling to inspect.

## Testing
```bash
python -m compileall scripts/orchestrate_zip_runs.py
pytest
```
Tests cover export utilities and resume helpers. Extend with scenario-specific fixtures as new functionality lands.

## Ethical Scraping Guidelines
- Respect Holosun's terms of service and acceptable use policies.
- Throttle requests (the orchestrator defaults to modest retry/backoff; avoid parallel runners without explicit approval).
- Rotate user agents and honor cookies provided by the site; never bypass explicit anti-automation defenses.
- Log provenance (`source_zip`) for every dealer and maintain audit trails in `data/raw/` for transparency.
- Pause runs when `logs/manual_attention.log` flags a CAPTCHA or block and obtain human approval before resuming.
- Do not distribute scraped data publicly unless stakeholder expectations and legal review permit it.

## Release Preparation
1. Run the orchestrator to completion (or resume until all ZIPs finish).
2. Review `run_state.json` / `run_summary.json` for outstanding blocked or errored ZIPs.
3. Verify the deliverable CSV and metrics JSON (spot-check rows, ensure schema compliance).
4. Update documentation (`docs/project-notes.md`, README, release checklist) with any workflow deviations.
5. Archive the run directory and manual-attention log output alongside the final CSV for auditability.
