# Project Context Summary

## Assignment Overview
- Objective: retrieve all Holosun dealers for California by iterating the locator via every CA ZIP code, deduplicate results, and export a clean CSV with provenance.
- Constraints: respect anti-automation defenses, surface manual-intervention prompts, and maintain detailed documentation throughout development.

## Key Decisions & Architecture
- Classified the project as backend/data-engineering; no UI beyond CLI tooling is planned.
- Discovery strategy: use Playwright-based recon script (`scripts/capture_locator_traffic.py`) to log XHR/fetch traffic, capture raw payloads to `data/raw/network/`, and emit YAML summaries before implementing the full scraper.
- ZIP Provider: automate sourcing of California ZIPs from the `scpike/us-state-county-zip` dataset, filtering to valid 5-digit entries, normalizing city/county casing, and persisting both CSV data and metadata artifacts (`scripts/fetch_ca_zip_codes.py`).
- Operator experience: run controller will report stage-by-stage progress (collecting ZIPs, submitting requests, normalizing, exporting), track metrics, and pause with instructions when anti-automation triggers occur.
- Data persistence plan: store raw payloads per ZIP for audit, normalize/de-duplicate into an in-memory or SQLite cache, then write `dealers.csv` and `zip_audit.csv` with traceable provenance.

## Implemented Artifacts
- `docs/project-notes.md`: living design/documentation, continuously updated (architecture, TODO backlog, change log).
- `scripts/capture_locator_traffic.py`: asynchronous Playwright utility ready for reconnaissance runs (not yet executed; TODO remains open).
- `scripts/fetch_ca_zip_codes.py`: CLI ingestion script producing `data/processed/ca_zip_codes.csv` (1,678 entries) and `data/processed/ca_zip_codes.metadata.json` with source details.
- Repository scaffolding: structured directories for docs, src, scripts, config, data, logs, tests; `.gitignore` tuned to include curated data artifacts.

## Current Work State (2025-10-08)
- Latest changes staged locally (awaiting commit approval): documentation updates, ZIP ingestion script, generated CSV/metadata, `.gitignore` adjustments.
- `Capture live network behavior` TODO remains unchecked because the recon script has not been run against the live site; executing it will provide the raw payload schema needed for the dealer data model.
- Next critical task: run `scripts/capture_locator_traffic.py` to observe actual API responses, then design the dealer data model and normalization logic based on real payloads.

## Outstanding TODO Highlights
- Execute reconnaissance capture and analyze response structure.
- Define dealer data model, normalization/deduplication strategy, and proof-of-concept fetcher.
- Implement progress-aware run orchestrator, resilience features (retry/backoff/manual prompts), CSV writer, tests, and delivery checklist.

## Open Risks & Questions
- Anti-automation measures may require manual intervention; need clarity on acceptable levels of human involvement for the assignment.
- Locator response structure or endpoints might shift; selectors and configs should remain configurable.
- Determine expectations for CI, hosting, or further deliverables in the public repository.

## Collaboration Notes
- Each completed TODO requires user approval before committing.
- Documentation is updated prior to implementing changes; commits must be staged and summarized before seeking approval.
- This file serves as a compact context snapshot for future collaboration sessions or new chat threads.
