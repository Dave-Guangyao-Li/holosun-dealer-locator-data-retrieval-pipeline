# Holosun Dealer Locator Data Collection - Project Notes
Last updated: 2025-10-08

## Executive Summary
- Project focus: backend-oriented data acquisition and processing pipeline delivering a curated CSV dataset. No frontend UI is planned beyond command-line tooling and documentation.
- Primary goal: enumerate all Holosun dealers discoverable through the locator for California ZIP codes, normalize the records, and export to CSV with provenance.
- Delivery expectation: repeatable scriptable workflow, structured documentation, and public repository transparency.

## Requirements and Constraints
- Assignment: submit every California ZIP to https://holosun.com/where-to-buy.html?c=both and export all dealer records (name, address, phone, website) to CSV.
- Output schema: `dealer_name`, `street`, `city`, `state`, `postal_code`, `phone`, `website`, `source_zip`, optional metadata columns (category, lat/lon) if present.
- Data nuances: duplicates across ZIP queries, multi-location dealers, variable address formats, missing phone/URL fields.
- Environment: initial design-only phase; actual scraping will require respectful automation, likely from a developer workstation with Playwright/Requests.
- Compliance: honor Holosun terms of service, stay within polite request volumes, log source ZIP for traceability.

## Architecture Overview
1. **Discovery and Recon**
   - Manual or scripted DevTools-style capture (e.g., Playwright with network logging) to enumerate request payloads, authentication headers, pagination behavior, and rate limits without yet writing the full scraper.
   - Maintain a dedicated script (see `scripts/capture_locator_traffic.py`) that runs headless, records request/response pairs to `data/raw/network/`, and emits a YAML summary for analysis.
   - Record any anti-bot signals (CAPTCHA, session tokens) and define how the automation will surface them to the operator.
2. **ZIP Code Provider**
   - Automate retrieval of a canonical California ZIP list from trusted open data (currently the `scpike/us-state-county-zip` GitHub dataset) using a lightweight ingestion script committed to `scripts/fetch_ca_zip_codes.py`.
   - Store fetched records in `data/processed/ca_zip_codes.csv` alongside a JSON metadata file that captures source URL, retrieval timestamp, and record count for auditability.
   - Persist as CSV/JSON and expose as reusable iterator in code; include checks that validate counts, required columns (ZIP, city, county), and de-duplicate entries prior to export while skipping non-standard ZIP placeholders.
3. **Retrieval Layer**
   - Preferred: Playwright (Python) to submit ZIPs, intercept XHR responses, and save raw dealer payloads.
   - Alternate: direct HTTP client (requests/httpx) if locator API is replayable.
   - Throttle requests (1–2 s jitter, limited concurrency), rotate user agents, honor cookies.
4. **Parsing and Normalization**
   - Convert raw responses into structured models; normalize addresses (usaddress), phones (phonenumbers), URLs (urlparse).
   - Generate stable dealer IDs (hash of name + normalized street + city) for deduplication.
5. **Persistence and Delivery**
   - Store raw per-ZIP payloads (newline JSON) for audit alongside a richer `normalized_dealers.json` snapshot that keeps IDs, timestamps, and geocodes for troubleshooting.
   - Aggregate normalized dealers in memory or SQLite before CSV export, flushing batches to disk periodically so long runs can resume without replaying completed ZIPs.
   - Auto-render the deliverable CSV (`holosun_ca_dealers.csv`) after each flush, trimmed to assignment-required columns (`dealer_name`, `street`, `city`, `state`, `postal_code`, `phone`, `website`, `source_zip`) while keeping metrics JSON and audit CSVs in the run directory.
6. **Quality and Observability**
   - Structured logging, retry with exponential backoff, summary report after run.
   - .env driven configuration for endpoints, rate limits, and logging levels.

## Network Reconnaissance Findings (2025-10-08)
- Headless Playwright runs against ZIPs `94105` and `90001` trigger a POST to `https://holosun.com/index/dealer/search.html` with `application/x-www-form-urlencoded` body parameters `keywords`, `distance`, `lat`, `lng`, and `cate` (default `both`).
- Request payload is derived from Google Maps geocoding (lat/lng pulled from `google.maps.Geocoder`), confirming we either need to geocode ZIPs ourselves or replay a cached coordinate table to avoid extra API calls.
- Successful responses return JSON shaped as `{"code": 1, "data": {"center": [lat, lng], "list": [...]}}`. Dealer objects currently expose:
  - identifiers (`id`, `category`, `status`, `create_time`, `update_time`, flags `is_military`/`is_civil`),
  - contact details (`company_name`, `contact_addr`, `phone`, `website`, `email`, `invoices_email`, `zip`),
  - location metadata (`lat`, `lng`, `contact` concatenated string),
  - optional commerce fields (`store_amazon`, `store_gunbroker`, `retail_num`, etc.) that frequently arrive empty.
- `contact_addr` is newline-delimited (`\n`) and phones include the literal `Phone:` prefix; both will require normalization before CSV export.
- Raw artifacts: JSON bodies live at `data/raw/network/20251008_94105/response_002.json` (empty hit) and `data/raw/network/20251008_90001/response_002.json` (single dealer example). Associated summaries and Playwright traces sit alongside each capture directory for audit.
- Non-dealer Google Maps telemetry (`mapsjs` RPC responses) is captured but not required for the pipeline; we can ignore during parsing once the dealer payload is isolated.

## Dealer Data Model & Normalization Plan (2025-10-08)
- **Canonical fields**: `dealer_id`, `holosun_id`, `company_name`, `street`, `suite`, `city`, `state`, `postal_code`, `phone`, `emails`, `website`, `source_zip`, `holosun_category`, `is_civil`, `is_military`, `holosun_lat`, `holosun_lng`, `first_seen_at`, `last_seen_at`, plus `raw_record_path` for provenance.
- **Source mapping**: `holosun_id` from `data.list[*].id`; `company_name` from `company_name`; address pieces parsed from `contact_addr` (primary) with fallback to `contact`; `phone` from `phone`/`tel`; `emails` from `email`/`invoices_email`; `website` from `website`/`sale_website`; geographic flags from boolean columns.
- **Normalization rules**:
  - Strip the `"Phone:"` prefix, collapse whitespace, and canonicalize formatting with `phonenumbers` when available.
  - Split email strings on commas, trim whitespace, deduplicate, and persist as a sorted `;`-delimited string to keep CSV compatibility while retaining multiple addresses.
  - Parse `contact_addr` via `usaddress.tag`, capturing street line and suite separately; enforce uppercase state `CA`, normalize city to title case, and validate ZIP against the five-digit pattern.
  - Preserve Holosun-provided lat/lng as floats and surface them in dedicated `holosun_lat`/`holosun_lng` columns without rounding to maintain fidelity for future spatial work.
  - Carry forward optional commerce links only when non-empty and document an extension column set for future enrichment (`store_amazon`, etc.) outside the primary CSV.
- **Deduplication approach**:
  - Compute `dealer_id` as a SHA256 hex digest of `company_name` (normalized) + `street` + `city` + `postal_code` to stay stable across minor payload shuffles.
  - Track `first_seen_at`/`last_seen_at` timestamps per dealer using capture metadata so repeated ZIP hits contribute to audit trails without multiplying rows.
  - Treat Holosun `id` as advisory only—retain it for troubleshooting but do not rely on it for uniqueness because IDs may recycle across categories or regions.
- **Audit strategy**: persist each raw JSON payload path in the structured record, and archive the normalized record to an intermediate SQLite table before rendering CSV to support repeatable quality checks.

## Geocoding Strategy Decision (2025-10-08)
- The Holosun frontend geocodes ZIPs through Google Maps at runtime, but relying on that flow would keep our automation tethered to an external API and introduce nondeterministic lat/lng drift.
- We enriched `data/processed/ca_zip_codes.csv` with offline centroids during ingestion so the pipeline can submit requests without hitting third-party geocoders.
- Playwright recon remains useful for capturing request payloads, yet the proof-of-concept fetcher will submit requests directly with the offline coordinates to avoid browser automation and third-party lookups during full runs.
- Holosun response coordinates stay authoritative for dealer point locations; the ZIP centroid is purely a request input and can be logged separately for transparency.

## ZIP Centroid Enrichment (2025-10-08)
- Updated `scripts/fetch_ca_zip_codes.py` to hydrate latitude/longitude from the OpenDataDE CA ZIP GeoJSON dataset, fall back to the nationwide USCities JSON file for non-ZCTA ZIPs, and apply a manual override for `91719` (Corona) using OSM Nominatim coordinates.
- Regenerated `data/processed/ca_zip_codes.csv` with populated `latitude`/`longitude` columns for all 1,678 California ZIPs; metadata now records both centroid sources and the override policy.
- Script logs flag non-standard placeholder ZIPs and surfaces counts for missing centroid matches so future source drift is immediately visible to operators.

## Single ZIP Fetcher Prototype (2025-10-08)
- Added `scripts/fetch_single_zip.py`, a proof-of-concept fetcher that reads centroid coordinates from `data/processed/ca_zip_codes.csv`, submits Holosun dealer lookups via direct POST requests, and raises explicit `AntiAutomationError` exceptions when responses resemble blocks (non-JSON content, non-`code=1` payloads, 4xx/5xx statuses, or common CAPTCHA phrases).
- The script persists artifacts under `data/raw/single_zip_runs/<timestamp>_<zip>/`, capturing the request payload/headers, full JSON response, and a normalized dealer summary (company name, contact lines, cleaned phone, web URL, lat/lon, Holosun ID, source ZIP).
- Normalization currently focuses on trimming phone prefixes, collapsing multiline addresses, and preserving emails for future enrichment; broader deduplication and persistence remain orchestrator responsibilities.
- CLI supports `--skip-write` for smoke tests, configurable distance/category/user agent parameters, and defaults to the enriched centroid dataset so no live geocoder is required during requests.

## Stage-Orchestrator Prototype (2025-10-08)
- Added `scripts/orchestrate_zip_runs.py`, a stage-aware controller that loads centroid metadata, iterates requested ZIP codes, and funnels each lookup through the single-ZIP fetch flow while emitting `[stage:*]` progress logs.
- Aggregates normalized dealers through a SHA256-based dedup key (`dealer_name` + street + city + postal code), tracking `first_seen_at`/`last_seen_at`, origin ZIPs, run IDs, holosun IDs, emails, and merged address lines.
- Persists per-ZIP artifacts under `data/raw/orchestrator_runs/<run_id>/zip_runs/` (reusing the single ZIP artifact structure), records anti-automation hits to `blocked_zips/` plus `logs/manual_attention.log` (created lazily on first block), and writes `run_summary.json` with zip-by-zip metrics.
- Produces `normalized_dealers.json` capturing the deduplicated dealer catalog for downstream CSV export and validation tooling.
- Supports resilient retries via `--max-retries`, `--retry-delay`, and `--retry-backoff`, and can prompt interactively on blocked ZIPs when `--prompt-on-block` is supplied from a TTY, allowing operators to retry, skip, or abort without restarting the run.

## CSV Export and Validation (2025-10-08)
- Implemented `holosun_locator.exports` with helpers to load, validate, summarize, and serialize normalized dealer payloads.
- Added `scripts/export_normalized_dealers.py` CLI for exporting `normalized_dealers.json` to CSV, emitting spot-check metrics, and optionally writing metrics JSON; supports list delimiters and strict validation with `--fail-on-validation`.
- Spot metrics cover dealer counts, duplicate IDs, phone/email completeness, geocode coverage, and ZIP/run breadth to highlight gaps before delivery.
- Introduced `tests/test_exports.py` exercising validation, CSV serialization, and metrics calculations (running under `pytest`).

## Operator Feedback and Progress Reporting
- Provide a high-level run controller that surfaces stage-based updates (e.g., collecting ZIPs, submitting locator requests, normalizing data, exporting CSV) to stdout and structured logs.
- Emit progress metrics such as processed ZIP count, dealer records accumulated, and retry/backoff events to keep the operator informed in long runs.
- When errors or anti-automation blocks occur, display clear instructions on next actions, capture the context in `logs/`, and optionally pause for manual input when `--prompt-on-block` is enabled and a TTY is available.
- Persist a run summary artifact (JSON or Markdown) outlining total steps, successes/failures, and manual follow-ups required.

## Anti-Automation Mitigation Strategy
- **Detection**: monitor response payloads/status codes for CAPTCHA markers, HTML challenge pages, or unusual HTTP codes (429, 403, 503).
- **Manual Intervention Hooks**:
  - Implement explicit checkpoints that surface actionable prompts when detection triggers (e.g., pause run, write to console and log instructions for manual resolution).
  - Provide CLI flag `--prompt-on-block` to prompt the operator before retrying blocked ZIPs; exponential backoff retries run automatically when prompts are skipped or disabled.
  - Persist blocked ZIPs to `logs/manual_attention.log` for follow-up.
- **Fallbacks**: allow operator to inject fresh session cookies or swap to manual browser export for affected ZIPs.

## Project Classification
- Categorized as a **backend/data-engineering project**: emphasis on automation, normalization, and data delivery with no dedicated UI layer.
- Potential lightweight CLI or API endpoints could be optional future enhancements but are out of initial scope.

## TODO Backlog
- [x] Capture live network behavior of the dealer locator via `scripts/capture_locator_traffic.py`, storing request/response payloads and annotated summaries. (2025-10-08 headless runs for ZIP 94105/90001 captured under `data/raw/network/20251008_*`.)
- [x] Implemented `scripts/fetch_ca_zip_codes.py` to download and validate California ZIP data, exporting to `data/processed/ca_zip_codes.csv` with source metadata.
- [x] Documented dealer data model, normalization rules, and deduplication key strategy (2025-10-08).
- [x] Build proof-of-concept fetcher for a single ZIP including anti-automation detection hooks. (2025-10-08 via `scripts/fetch_single_zip.py` writing to `data/raw/single_zip_runs/`.)
- [x] Implement stage-aware run orchestrator that reports progress, surfaces manual-intervention prompts, and stores run summaries. (2025-10-08 via `scripts/orchestrate_zip_runs.py`.)
- [x] Implement stateful deduplication and accumulator for merging records across ZIPs. (2025-10-08 orchestrator ingest pipeline emits `normalized_dealers.json`.)
- [x] Add resilient retry/backoff, logging, and manual-intervention prompts when encountering blocks. (2025-10-08: orchestrator `--max-retries`/`--retry-delay`/`--prompt-on-block` flow.)
- [x] Create CSV writer and validation scripts (spot-checks, summary metrics). (2025-10-08: `scripts/export_normalized_dealers.py` + `holosun_locator.exports`.)
- [ ] Automate CSV export and metrics emission at the end of orchestrator runs.
- [ ] Design resume tooling to replay blocked ZIPs from `logs/manual_attention.log` or run summaries.
- [ ] Draft README with setup, run instructions, and ethical scraping guidelines.
- [ ] Prepare release checklist (data validation, documentation updates, final CSV verification).
- [x] Enriched `data/processed/ca_zip_codes.csv` with offline ZIP centroid latitude/longitude for request payloads (2025-10-08).

## Risks and Open Questions
- Anti-automation defenses may require human-in-the-loop operations; need clarity on acceptable manual steps for the assignment.
- Locator may limit results per ZIP, forcing radius adjustments or supplemental queries.
- Site structure could change; isolate selectors, endpoints, and configs for maintainability.
- Need decision on hosting/CI expectations for the public repo (e.g., GitHub Actions for lint/tests?).
- Confirm whether downstream consumers expect geocoding or map visualization (currently out of scope).

## Change Log
- **2025-10-08**: Documented the final handoff CSV schema, outlined incremental batch flushing + resume strategy, and planned automatic exporter/metrics hooks inside the orchestrator.
- **2025-10-08**: Hardened the run orchestrator with configurable retry/backoff plus interactive prompts, added CSV export/validation tooling with metrics JSON output, and introduced pytest coverage for the exporter utilities.
- **2025-10-08**: Executed Playwright recon for ZIPs 94105 and 90001, refreshed `scripts/capture_locator_traffic.py` selectors/response waiting, archived raw payloads under `data/raw/network/20251008_*`, and documented the dealer API envelope plus normalization considerations.
- **2025-10-08**: Enriched CA ZIP reference data with centroid coordinates via OpenDataDE GeoJSON + USCities fallback, added manual overrides for outliers, and regenerated processed CSV/metadata.
- **2025-10-08**: Shipped `scripts/fetch_single_zip.py`, delivering offline-centroid driven single-ZIP lookups, anti-automation detection, and normalized artifact dumps under `data/raw/single_zip_runs/`.
- **2025-10-08**: Introduced `scripts/orchestrate_zip_runs.py`, providing stage-aware ZIP iteration, deduplicated dealer accumulation, anti-automation routing, and consolidated run artifacts under `data/raw/orchestrator_runs/`.
- **Prior session**: Added anti-automation mitigation plan, clarified backend classification, consolidated architecture summary with automation-first recon/ZIP sourcing guidance and dedicated network capture script plan, documented operator progress reporting expectations, tightened ZIP sourcing implementation details, shipped automated CA ZIP ingestion artifacts, curated TODO backlog, scaffolded repository structure.
