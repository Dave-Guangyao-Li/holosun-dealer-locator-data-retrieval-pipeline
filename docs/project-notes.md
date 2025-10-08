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
   - Store raw per-ZIP payloads (newline JSON) for audit.
   - Aggregate normalized dealers in memory or SQLite before CSV export.
   - Write final `dealers.csv` sorted by dealer name then city, plus `zip_audit.csv` summarizing counts per ZIP.
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

## Operator Feedback and Progress Reporting
- Provide a high-level run controller that surfaces stage-based updates (e.g., collecting ZIPs, submitting locator requests, normalizing data, exporting CSV) to stdout and structured logs.
- Emit progress metrics such as processed ZIP count, dealer records accumulated, and retry/backoff events to keep the operator informed in long runs.
- When errors or anti-automation blocks occur, display clear instructions on next actions, capture the context in `logs/`, and optionally pause for manual input when `--interactive` is enabled.
- Persist a run summary artifact (JSON or Markdown) outlining total steps, successes/failures, and manual follow-ups required.

## Anti-Automation Mitigation Strategy
- **Detection**: monitor response payloads/status codes for CAPTCHA markers, HTML challenge pages, or unusual HTTP codes (429, 403, 503).
- **Manual Intervention Hooks**:
  - Implement explicit checkpoints that surface actionable prompts when detection triggers (e.g., pause run, write to console and log instructions for manual resolution).
  - Provide CLI flag `--interactive` to prompt operator before retrying blocked ZIPs.
  - Persist blocked ZIPs to `logs/manual_attention.log` for follow-up.
- **Fallbacks**: allow operator to inject fresh session cookies or swap to manual browser export for affected ZIPs.

## Project Classification
- Categorized as a **backend/data-engineering project**: emphasis on automation, normalization, and data delivery with no dedicated UI layer.
- Potential lightweight CLI or API endpoints could be optional future enhancements but are out of initial scope.

## TODO Backlog
- [x] Capture live network behavior of the dealer locator via `scripts/capture_locator_traffic.py`, storing request/response payloads and annotated summaries. (2025-10-08 headless runs for ZIP 94105/90001 captured under `data/raw/network/20251008_*`.)
- [x] Implemented `scripts/fetch_ca_zip_codes.py` to download and validate California ZIP data, exporting to `data/processed/ca_zip_codes.csv` with source metadata.
- [ ] Define dealer data model, normalization rules, and deduplication key strategy.
- [ ] Build proof-of-concept fetcher for a single ZIP including anti-automation detection hooks.
- [ ] Implement stage-aware run orchestrator that reports progress, surfaces manual-intervention prompts, and stores run summaries.
- [ ] Implement stateful deduplication and accumulator for merging records across ZIPs.
- [ ] Add resilient retry/backoff, logging, and manual-intervention prompts when encountering blocks.
- [ ] Create CSV writer and validation scripts (spot-checks, summary metrics).
- [ ] Draft README with setup, run instructions, and ethical scraping guidelines.
- [ ] Prepare release checklist (data validation, documentation updates, final CSV verification).

## Risks and Open Questions
- Anti-automation defenses may require human-in-the-loop operations; need clarity on acceptable manual steps for the assignment.
- Locator may limit results per ZIP, forcing radius adjustments or supplemental queries.
- Site structure could change; isolate selectors, endpoints, and configs for maintainability.
- Need decision on hosting/CI expectations for the public repo (e.g., GitHub Actions for lint/tests?).
- Confirm whether downstream consumers expect geocoding or map visualization (currently out of scope).

## Change Log
- **2025-10-08**: Executed Playwright recon for ZIPs 94105 and 90001, refreshed `scripts/capture_locator_traffic.py` selectors/response waiting, archived raw payloads under `data/raw/network/20251008_*`, and documented the dealer API envelope plus normalization considerations.
- **Prior session**: Added anti-automation mitigation plan, clarified backend classification, consolidated architecture summary with automation-first recon/ZIP sourcing guidance and dedicated network capture script plan, documented operator progress reporting expectations, tightened ZIP sourcing implementation details, shipped automated CA ZIP ingestion artifacts, curated TODO backlog, scaffolded repository structure.
