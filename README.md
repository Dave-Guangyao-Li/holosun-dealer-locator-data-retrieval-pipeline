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

## Step-by-Step Delivery Workflow

### 1. Prepare Reference Data (required once per refresh)
```bash
python scripts/fetch_ca_zip_codes.py \
  --output data/processed/ca_zip_codes.csv
```
Expected results:
- `data/processed/ca_zip_codes.csv` (≈1,678 rows with latitude/longitude columns).
- `data/processed/ca_zip_codes.metadata.json` capturing source URLs, timestamps, and row counts.
- Console log ends with `INFO Loaded <count> ZIP records`.

Optional smoke test before the full batch:
```bash
python scripts/fetch_single_zip.py 90001 --verbose
```
Outputs an artifact directory like `data/raw/single_zip_runs/20251009T010203Z_90001/` containing the request payload, raw response, and a normalized summary.

### 2. Run the Orchestrator (fresh batch)

```bash
python scripts/orchestrate_zip_runs.py \
  --zip-csv data/processed/ca_zip_codes.csv \
  --flush-every 25 \
  --deliverable-name holosun_ca_dealers.csv
```

What to expect:
- Console `INFO` lines indicating stages (`[stage:load_zip_table]`, `[stage:submit_locator_request]`, etc.).
- A run directory `data/raw/orchestrator_runs/<run_id>/` (example `20251009T023000Z/`).
  - `run_state.json` tracks progress during the run.
  - `run_summary.json` appears at completion.
  - `normalized_dealers.json` / `normalized_dealers.csv` hold full normalized records.
  - `holosun_ca_dealers.csv` (trimmed deliverable) and `<deliverable>.metrics.json` expose final outputs.
  - When `--skip-raw` is omitted, `zip_runs/<zip>` folders store per-ZIP artifacts.
- Metrics log lines similar to `Metrics snapshot: total=185, unique=175, with_phone=140` during each flush.

Recommended operating tips:
- Use `--prompt-on-block` to manually confirm retries when anti-automation is detected.
- Increase `--flush-every` for lighter disk usage; decrease for more frequent checkpoints.
- Supply `--max-zips` while testing to cap runtime.

### 3. Resume or Replay Scenarios

#### Scenario A — Resume after interruption
```bash
python scripts/orchestrate_zip_runs.py \
  --zip-csv data/processed/ca_zip_codes.csv \
  --resume-state data/raw/orchestrator_runs/20251009T023000Z/run_state.json
```
Default `--resume-policy skip` drops ZIPs already completed. Console output confirms: `INFO Skipping <N> ZIPs already completed in resume state`. Deliverables refresh even if no new ZIPs remain.

#### Scenario B — Replay only blocked ZIPs
```bash
python scripts/orchestrate_zip_runs.py \
  --resume-state data/raw/orchestrator_runs/20251009T023000Z/run_state.json \
  --resume-policy blocked \
  --include-manual-log \
  --manual-log-run 20251009T023000Z
```
The orchestrator loads blocked ZIPs from `run_state.json` and merges any matching entries in `logs/manual_attention.log`. Expect `INFO Loaded <count> ZIPs from manual attention log` followed by `Processing <count> ZIP codes` limited to the problem set.

#### Scenario C — Full rerun with manual overrides
```bash
python scripts/orchestrate_zip_runs.py \
  --zip 90001 --zip 94105 \
  --include-manual-log \
  --manual-log logs/custom_manual_attention.log \
  --deliverable-name rerun_holosun_ca_dealers.csv
```
Mixing explicit `--zip` values with manual-log entries is useful for ad-hoc validations. Ensure the referenced log exists; otherwise, the tool warns and skips missing ZIPs.

### 4. Re-export or Validate Deliverables (optional)

```bash
python scripts/export_normalized_dealers.py \
  --input data/raw/orchestrator_runs/20251009T023000Z/normalized_dealers.json \
  --output data/raw/orchestrator_runs/20251009T023000Z/normalized_dealers.csv \
  --metrics-json data/raw/orchestrator_runs/20251009T023000Z/normalized_dealers.metrics.json
```
Use this when consumers request a reformatted CSV or independent metrics run. Expected console output echoes record counts and where files were written.

### 5. Post-Run Checklist
- Review `run_summary.json` for non-zero `blocked_count` or `error_count`.
- Inspect `logs/manual_attention.log` lines for unresolved issues.
- Spot-check `holosun_ca_dealers.csv` for schema and data cleanliness (no embedded newlines, consistent ZIP padding).
- Compare `<deliverable>.metrics.json` against prior runs for anomalies.
- Complete the full release checklist in `docs/release-checklist.md` before distributing artifacts.

## Observability & Output
- Logging uses the `holosun.*` namespace. Pass `--verbose` to surface debug traces.
- Each batch flush recomputes deliverable CSV and metrics, ensuring resumable runs yield consistent artifacts even if interrupted.
- Blocked ZIPs create structured JSON entries in `logs/manual_attention.log` and corresponding files inside `<run_dir>/blocked_zips/`.
- `run_state.json` always contains the latest accumulator snapshot location, making it safe to resume mid-run.

## Troubleshooting Quick Reference
- **Missing centroid row**: Ensure `data/processed/ca_zip_codes.csv` exists and rerun the fetch script.
- **CAPTCHA/blocked response**: Use `--prompt-on-block`, check manual log entries, and retry with a slower cadence or new session cookies.
- **Deliverable appears empty**: Verify that `normalized_dealers.json` contains data and that ZIP selection (`--zip`, `--max-zips`) was correct.
- **Metrics JSON missing**: Confirm `--deliverable-name` and `--metrics-name` paths are writable; rerun the export helper if needed.

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

## Release Preparation (quick view)
1. Run the orchestrator to completion (or resume until all ZIPs finish).
2. Review `run_state.json` / `run_summary.json` for outstanding blocked or errored ZIPs.
3. Verify the deliverable CSV and metrics JSON (spot-check rows, ensure schema compliance).
4. Update documentation (`docs/project-notes.md`, README, release checklist) with any workflow deviations.
5. Archive the run directory and manual-attention log output alongside the final CSV for auditability.

Full checklist: `docs/release-checklist.md`.
