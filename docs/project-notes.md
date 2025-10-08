# Holosun Dealer Locator Data Collection - Project Notes
Last updated: current session

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
   - Automate retrieval of a canonical California ZIP list from trusted open data (e.g., public Opendatasoft API, Census downloads) using a lightweight ingestion script committed to `scripts/`.
   - Persist as CSV/JSON and expose as reusable iterator in code; include checks that validate counts and de-duplicate entries.
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
- [ ] Capture live network behavior of the dealer locator via `scripts/capture_locator_traffic.py`, storing request/response payloads and annotated summaries.
- [ ] Compile authoritative list of California ZIP codes; validate counts and deduplicate.
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
- **Current session**: Added anti-automation mitigation plan, clarified backend classification, consolidated architecture summary with automation-first recon/ZIP sourcing guidance and dedicated network capture script plan, documented operator progress reporting expectations, curated TODO backlog, scaffolded repository structure.
