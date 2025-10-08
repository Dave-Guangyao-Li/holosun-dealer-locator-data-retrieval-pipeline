# Holosun Dealer Dataset Release Checklist

Use this list before publishing a refreshed dealer dataset or handing off artifacts to stakeholders.

## Pre-Run Preparation
- [ ] Confirm Python environment is activated and dependencies (`requests`, `pytest`) are up to date.
- [ ] Refresh California ZIP centroid data via `python scripts/fetch_ca_zip_codes.py` and validate record count against prior run metadata.
- [ ] Review `logs/manual_attention.log` for outstanding blocks; resolve or document mitigation steps before launching a new batch.
- [ ] Update `docs/project-notes.md` with any scope or requirement changes.

## Execute Orchestrator
- [ ] Launch the orchestrator with a deliberate `--flush-every` cadence (default 25) suitable for the expected runtime.
- [ ] Capture the exact CLI invocation in `docs/project-notes.md` for reproducibility.
- [ ] Monitor for anti-automation warnings; pause (`--prompt-on-block`) or abort when required.
- [ ] If the run is interrupted, resume using `--resume-state <run_dir>/run_state.json` with an appropriate `--resume-policy`.

## Post-Run Validation
- [ ] Inspect `<run_dir>/run_summary.json` for non-zero `blocked_count` or `error_count`; address any remaining ZIPs via replay or documented waivers.
- [ ] Spot-check `normalized_dealers.json` and the trimmed `holosun_ca_dealers.csv` for schema compliance (headers, required columns populated, no obvious duplicates).
- [ ] Review metrics JSON (dealer totals, phone/email coverage) and compare against previous runs for anomalies.
- [ ] Ensure deliverable CSV contains only assignment-approved columns and sanitized string values (no newline artifacts).

## Documentation & Packaging
- [ ] Update `docs/project-notes.md` change log with run highlights, blockers, and mitigation steps.
- [ ] Refresh README sections if workflow, dependencies, or ethical guidance changed.
- [ ] Store final artifacts together: deliverable CSV, metrics JSON, `run_summary.json`, `run_state.json`, manual-attention log excerpt, and validation notes.
- [ ] Record any manual adjustments or data corrections in `docs/project-notes.md` and `docs/project-context.md`.

## Sign-Off
- [ ] Confirm stakeholders agree on data quality, coverage, and delivery format.
- [ ] Archive the run directory for audit (compressed in `data/releases/<timestamp>.tar.gz` or an equivalent location).
- [ ] Tag the repository or commit SHA corresponding to the release state once approval is granted.
