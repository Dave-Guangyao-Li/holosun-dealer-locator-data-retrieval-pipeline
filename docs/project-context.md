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
- Data persistence plan: store raw payloads per ZIP for audit, keep a richer `normalized_dealers.json` for provenance, and continuously refresh the deliverable `holosun_ca_dealers.csv` (trimmed to assignment-required columns and filtered to California-only rows) plus metrics/json summaries as batches complete.

## Implemented Artifacts
- `docs/project-notes.md`: living design/documentation, continuously updated (architecture, TODO backlog, change log).
- `scripts/capture_locator_traffic.py`: asynchronous Playwright recon utility (headless by default) now tuned to the current Holosun DOM selectors and response timing; outputs JSON/YAML summaries plus raw bodies under `data/raw/network/<timestamp_zip>/`.
- `scripts/fetch_ca_zip_codes.py`: CLI ingestion script producing `data/processed/ca_zip_codes.csv` (1,678 entries with latitude/longitude populated) and `data/processed/ca_zip_codes.metadata.json` logging primary, fallback, and override sources.
- `scripts/fetch_single_zip.py`: proof-of-concept fetcher that reads offline centroids, submits direct POST requests, performs anti-automation checks, and writes request/response/normalized artifacts to `data/raw/single_zip_runs/`.
- `scripts/orchestrate_zip_runs.py`: stage-aware controller that sequences ZIP iteration, handles anti-automation detections with configurable retry/backoff and optional interactive prompts, accumulates deduplicated dealer records, and writes run artifacts to `data/raw/orchestrator_runs/<run_id>/`.
- `scripts/export_normalized_dealers.py` + `holosun_locator.exports`: CSV export/validation pipeline that converts `normalized_dealers.json` into the reduced handoff schema, emits spot-check metrics, and enforces validation; accompanied by pytest coverage under `tests/test_exports.py`.
- Repository scaffolding: structured directories for docs, src, scripts, config, data, logs, tests; `.gitignore` tuned to include curated data artifacts.

## Current Work State (2025-10-08)
- Latest changes staged locally (awaiting commit approval): recon script selector updates, new network capture artifacts under `data/raw/network/20251008_*`, centroid-enriched ZIP dataset regeneration, and documentation refreshes (dealer data model, normalization, deduplication, geocoding decisions).
- Playwright recon completed headless runs for ZIPs 94105 (no dealers) and 90001 (one dealer), confirming the POST `https://holosun.com/index/dealer/search.html` request shape (`keywords`, `distance`, `lat`, `lng`, `cate`) and the response schema (`data.center`, `data.list[*]`).
- Latest POC: single-ZIP fetcher validates the offline centroid workflow, surfaces anti-automation issues explicitly, and stores normalized summaries for quick inspection.
- Stage-aware orchestrator now exposes configurable retries/backoff and interactive prompts, producing per-run summaries plus deduplicated dealer catalogs ready for export.
- Export pipeline (`scripts/export_normalized_dealers.py`) operational for turning `normalized_dealers.json` into CSV and metrics artifacts; validation helpers and pytest coverage confirm schema expectations.
- Orchestrator persistence now auto-refreshes deliverables/metrics on each flush, saves `run_state.json`, and supports resumable runs via `--resume-state`/`--resume-policy` with optional blocked ZIP replay from `logs/manual_attention.log`.
- Operator documentation refreshed: README now covers setup/run guidance and `docs/release-checklist.md` captures the pre-flight validation steps before publishing datasets.
- Address parsing refinements now extract street/city/state/ZIP from single-line payloads, eliminate source-ZIP duplicates in the accumulator, shrink the deliverable schema to `dealer_name/address/phone/website`, and enforce a California-only filter (state or ZIP range) when exporting the final CSV.

## Outstanding TODO Highlights
- Backlog cleared for this milestone; future work will be tracked via new tickets as requirements evolve.

## Open Risks & Questions
- Anti-automation measures may require manual intervention; need clarity on acceptable levels of human involvement for the assignment.
- Locator response structure or endpoints might shift; selectors and configs should remain configurable.
- Determine expectations for CI, hosting, or further deliverables in the public repository.

## Collaboration Notes
- Each completed TODO requires user approval before committing.
- Documentation is updated prior to implementing changes; commits must be staged and summarized before seeking approval.
- This file serves as a compact context snapshot for future collaboration sessions or new chat threads.
