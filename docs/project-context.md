# Project Context Summary

## Assignment Overview
- Objective: retrieve all Holosun dealers for California by iterating the locator via every CA ZIP code, deduplicate results, and export a clean CSV with provenance.
- Constraints: respect anti-automation defenses, surface manual-intervention prompts, and maintain detailed documentation throughout development.

## Key Decisions & Architecture
- Classified the project as backend/data-engineering; no UI beyond CLI tooling is planned.
- Discovery strategy: use Playwright-based recon script (`scripts/capture_locator_traffic.py`) to log XHR/fetch traffic, capture raw payloads to `data/raw/network/`, and emit YAML summaries before implementing the full scraper.
- ZIP Provider: automate sourcing of California ZIPs from the `scpike/us-state-county-zip` dataset, filtering to valid 5-digit entries, normalizing city/county casing, and persisting both CSV data and metadata artifacts (`scripts/fetch_ca_zip_codes.py`). Centroid enrichment now hydrates latitude/longitude via OpenDataDE GeoJSON, USCities JSON fallbacks, and targeted manual overrides for outlier ZIPs.
- Dealer records will capture normalized address/phone/email fields plus Holosun-provided IDs and lat/lng; deduplication hinges on a SHA256 of normalized name + street + city + postal code while tracking first/last seen timestamps for auditability.
- Request geocoding will rely on offline ZIP centroids bundled with our dataset so Playwright is not required for production runs; Holosun response coordinates remain the authoritative dealer points we export.
- Operator experience: run controller will report stage-by-stage progress (collecting ZIPs, submitting requests, normalizing, exporting), track metrics, and pause with instructions when anti-automation triggers occur.
- Data persistence plan: store raw payloads per ZIP for audit, normalize/de-duplicate into an in-memory or SQLite cache, then write `dealers.csv` and `zip_audit.csv` with traceable provenance.

## Implemented Artifacts
- `docs/project-notes.md`: living design/documentation, continuously updated (architecture, TODO backlog, change log).
- `scripts/capture_locator_traffic.py`: asynchronous Playwright recon utility (headless by default) now tuned to the current Holosun DOM selectors and response timing; outputs JSON/YAML summaries plus raw bodies under `data/raw/network/<timestamp_zip>/`.
- `scripts/fetch_ca_zip_codes.py`: CLI ingestion script producing `data/processed/ca_zip_codes.csv` (1,678 entries with latitude/longitude populated) and `data/processed/ca_zip_codes.metadata.json` logging primary, fallback, and override sources.
- `scripts/fetch_single_zip.py`: proof-of-concept fetcher that reads offline centroids, submits direct POST requests, performs anti-automation checks, and writes request/response/normalized artifacts to `data/raw/single_zip_runs/`.
- `scripts/orchestrate_zip_runs.py`: stage-aware controller that sequences ZIP iteration, handles anti-automation detections, accumulates deduplicated dealer records, and writes run artifacts to `data/raw/orchestrator_runs/<run_id>/`.
- Repository scaffolding: structured directories for docs, src, scripts, config, data, logs, tests; `.gitignore` tuned to include curated data artifacts.

## Current Work State (2025-10-08)
- Latest changes staged locally (awaiting commit approval): recon script selector updates, new network capture artifacts under `data/raw/network/20251008_*`, centroid-enriched ZIP dataset regeneration, and documentation refreshes (dealer data model, normalization, deduplication, geocoding decisions).
- Playwright recon completed headless runs for ZIPs 94105 (no dealers) and 90001 (one dealer), confirming the POST `https://holosun.com/index/dealer/search.html` request shape (`keywords`, `distance`, `lat`, `lng`, `cate`) and the response schema (`data.center`, `data.list[*]`).
- Latest POC: single-ZIP fetcher validates the offline centroid workflow, surfaces anti-automation issues explicitly, and stores normalized summaries for quick inspection.
- Stage-aware orchestrator now available via `scripts/orchestrate_zip_runs.py`, producing per-run summaries plus deduplicated dealer catalogs ready for CSV export.

## Outstanding TODO Highlights
- Layer retry/backoff strategies plus optional interactive prompts onto the orchestrator so blocked ZIPs can be retried or queued for manual handling without aborting the run.
- Add CSV export/validation tooling, expand logging and tests, and finalize release documentation ahead of full pipeline runs.

## Open Risks & Questions
- Anti-automation measures may require manual intervention; need clarity on acceptable levels of human involvement for the assignment.
- Locator response structure or endpoints might shift; selectors and configs should remain configurable.
- Determine expectations for CI, hosting, or further deliverables in the public repository.

## Collaboration Notes
- Each completed TODO requires user approval before committing.
- Documentation is updated prior to implementing changes; commits must be staged and summarized before seeking approval.
- This file serves as a compact context snapshot for future collaboration sessions or new chat threads.
