# Scripts Directory

Command-line entry points and utilities that orchestrate the scraping pipeline.

## Available Scripts
- `fetch_ca_zip_codes.py` — downloads and enriches California ZIP metadata with offline centroid coordinates.
- `fetch_single_zip.py` — submits a single Holosun dealer lookup using offline centroids, performing anti-automation detection and emitting normalized artifacts.
- `orchestrate_zip_runs.py` — stage-aware runner that iterates ZIP codes, routes requests through the single-ZIP fetcher, accumulates deduplicated dealer records, and writes run summaries plus normalized outputs (configurable retry/backoff via `--max-retries`/`--retry-delay`, optional prompts with `--prompt-on-block`).
- `export_normalized_dealers.py` — converts `normalized_dealers.json` into CSV, runs validation checks, emits spot metrics, and optionally writes metrics JSON for downstream QA.
- `capture_locator_traffic.py` — Playwright-based reconnaissance utility for capturing raw network payloads from the Holosun dealer locator UI.
