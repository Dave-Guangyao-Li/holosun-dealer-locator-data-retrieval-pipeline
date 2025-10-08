"""Stage-aware orchestrator for iterating California ZIP codes.

The orchestrator coordinates multi-ZIP executions against the Holosun dealer
locator using offline centroids. It provides stage progress updates, performs
anti-automation checks, accumulates normalized dealer records with deduplication,
and persists both raw artifacts and run summaries under
``data/raw/orchestrator_runs/<timestamp>/``.
"""
from __future__ import annotations

import argparse
import sys
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from scripts.fetch_single_zip import (
    AntiAutomationError,
    detect_anti_automation,
    load_zip_centroids,
    normalize_dealer,
    perform_request,
    prepare_payload,
    write_artifacts,
)

from holosun_locator.exports import (
    compute_metrics,
    export_dealers_to_csv,
    validate_dealers,
)

LOGGER = logging.getLogger("holosun.orchestrator")
CITY_STATE_ZIP_RE = re.compile(r"^(?P<city>.*?)(?:,\s*(?P<state>[A-Z]{2}))?\s+(?P<postal>\d{5})(?:-\d{4})?$")
DEFAULT_OUTPUT_DIR = Path("data/raw/orchestrator_runs")
DEFAULT_MANUAL_LOG = Path("logs/manual_attention.log")
DEFAULT_FLUSH_EVERY = 25
DEFAULT_DELIVERABLE_NAME = "holosun_ca_dealers.csv"
DEFAULT_LIST_DELIMITER = "|"
RUN_STATE_FILENAME = "run_state.json"
NORMALIZED_JSON_NAME = "normalized_dealers.json"
NORMALIZED_CSV_NAME = "normalized_dealers.csv"
DELIVERABLE_FIELDNAMES: Sequence[str] = (
    "dealer_name",
    "street",
    "city",
    "state",
    "postal_code",
    "phone",
    "website",
    "source_zip",
)


class Stage:
    LOAD_ZIPS = "load_zip_table"
    FETCH = "submit_locator_request"
    NORMALIZE = "normalize_records"
    PERSIST = "persist_artifacts"


@dataclass
class DealerAggregate:
    dealer_id: str
    dealer_name: Optional[str]
    street: Optional[str]
    city: Optional[str]
    state: Optional[str]
    postal_code: Optional[str]
    phone: Optional[str]
    website: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    address_text: Optional[str]
    address_lines: List[str]
    emails: List[str]
    source_zips: List[str]
    holosun_ids: List[Any]
    runs: List[str]
    first_seen_at: str
    last_seen_at: str

    @classmethod
    def from_normalized(
        cls,
        dealer_id: str,
        normalized: Dict[str, Any],
        *,
        zip_code: str,
        observed_at: str,
        run_reference: str,
    ) -> "DealerAggregate":
        street, city, state, postal = extract_address_components(normalized)
        return cls(
            dealer_id=dealer_id,
            dealer_name=normalized.get("dealer_name"),
            street=street,
            city=city,
            state=state,
            postal_code=postal,
            phone=normalized.get("phone"),
            website=normalized.get("website"),
            latitude=normalized.get("latitude"),
            longitude=normalized.get("longitude"),
            address_text=normalized.get("address_text"),
            address_lines=list(normalized.get("address_lines") or []),
            emails=[email for email in (normalized.get("emails") or []) if email],
            source_zips=sorted({zip_code, normalized.get("source_zip") or zip_code}),
            holosun_ids=[normalized.get("holosun_id")] if normalized.get("holosun_id") else [],
            runs=[run_reference],
            first_seen_at=observed_at,
            last_seen_at=observed_at,
        )

    def update(self, normalized: Dict[str, Any], *, zip_code: str, observed_at: str, run_reference: str) -> None:
        if normalized.get("dealer_name") and not self.dealer_name:
            self.dealer_name = normalized["dealer_name"]
        street, city, state, postal = extract_address_components(normalized)
        if street and not self.street:
            self.street = street
        if city and not self.city:
            self.city = city
        if state and not self.state:
            self.state = state
        if postal and not self.postal_code:
            self.postal_code = postal
        if normalized.get("phone") and not self.phone:
            self.phone = normalized.get("phone")
        if normalized.get("website") and not self.website:
            self.website = normalized.get("website")
        if normalized.get("latitude") and not self.latitude:
            self.latitude = normalized.get("latitude")
        if normalized.get("longitude") and not self.longitude:
            self.longitude = normalized.get("longitude")
        if normalized.get("address_text") and not self.address_text:
            self.address_text = normalized.get("address_text")
        if normalized.get("address_lines"):
            merged = list(dict.fromkeys(self.address_lines + list(normalized.get("address_lines") or [])))
            self.address_lines = merged
        if normalized.get("emails"):
            merged_emails = sorted({email for email in self.emails if email} | {email for email in normalized.get("emails") if email})
            self.emails = merged_emails
        new_id = normalized.get("holosun_id")
        if new_id:
            merged_ids = sorted({hid for hid in self.holosun_ids if hid} | {new_id})
            self.holosun_ids = merged_ids
        merged_zips = sorted({*self.source_zips, zip_code, normalized.get("source_zip") or zip_code})
        self.source_zips = merged_zips
        if run_reference not in self.runs:
            self.runs.append(run_reference)
        self.last_seen_at = observed_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dealer_id": self.dealer_id,
            "dealer_name": self.dealer_name,
            "street": self.street,
            "city": self.city,
            "state": self.state,
            "postal_code": self.postal_code,
            "phone": self.phone,
            "website": self.website,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "address_text": self.address_text,
            "address_lines": self.address_lines,
            "emails": self.emails,
            "source_zips": self.source_zips,
            "holosun_ids": self.holosun_ids,
            "runs": self.runs,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
        }


def extract_address_components(normalized: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    lines = list(normalized.get("address_lines") or [])
    street = lines[0].strip() if lines else None
    city = state = postal = None
    for line in lines[1:]:
        match = CITY_STATE_ZIP_RE.match(line.strip())
        if match:
            city = match.group("city").strip() or None
            state = match.group("state") or None
            postal = match.group("postal") or None
            break
    if not postal:
        postal = (normalized.get("record_zip") or normalized.get("source_zip") or "").strip() or None
    return street, city, state, postal


class DealerAccumulator:
    def __init__(self) -> None:
        self._records: Dict[str, DealerAggregate] = {}

    def ingest(self, dealers: Iterable[Dict[str, Any]], *, zip_code: str, observed_at: str, run_reference: str) -> Tuple[int, int]:
        total = 0
        new_records = 0
        for dealer in dealers:
            total += 1
            dealer_id = compute_dealer_id(dealer)
            aggregate = self._records.get(dealer_id)
            if aggregate:
                aggregate.update(dealer, zip_code=zip_code, observed_at=observed_at, run_reference=run_reference)
            else:
                self._records[dealer_id] = DealerAggregate.from_normalized(
                    dealer_id,
                    dealer,
                    zip_code=zip_code,
                    observed_at=observed_at,
                    run_reference=run_reference,
                )
                new_records += 1
        return total, new_records

    def to_list(self) -> List[Dict[str, Any]]:
        return [aggregate.to_dict() for aggregate in self._records.values()]

    def __len__(self) -> int:
        return len(self._records)


def build_deliverable_rows(
    dealers: Sequence[Dict[str, Any]],
    *,
    list_delimiter: str,
) -> List[Dict[str, Any]]:
    """Transform normalized dealers into the trimmed deliverable schema."""

    rows: List[Dict[str, Any]] = []
    for dealer in dealers:
        source_zips = dealer.get("source_zips") or []
        if isinstance(source_zips, (list, tuple)):
            source_zip_value = list_delimiter.join(str(zip_code) for zip_code in source_zips if zip_code)
        elif source_zips is None:
            source_zip_value = ""
        else:
            source_zip_value = str(source_zips)

        rows.append(
            {
                "dealer_name": dealer.get("dealer_name") or "",
                "street": dealer.get("street") or "",
                "city": dealer.get("city") or "",
                "state": dealer.get("state") or "",
                "postal_code": dealer.get("postal_code") or "",
                "phone": dealer.get("phone") or "",
                "website": dealer.get("website") or "",
                "source_zip": source_zip_value,
            }
        )
    return rows


def compute_dealer_id(dealer: Dict[str, Any]) -> str:
    street, city, _, postal = extract_address_components(dealer)
    parts = [
        (dealer.get("dealer_name") or "").strip().lower(),
        (street or "").strip().lower(),
        (city or "").strip().lower(),
        (postal or "").strip(),
    ]
    digest_input = "|".join(parts).encode("utf-8")
    return hashlib.sha256(digest_input).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--zip-csv",
        type=Path,
        default=Path("data/processed/ca_zip_codes.csv"),
        help="CSV file containing ZIP centroid data.",
    )
    parser.add_argument(
        "--zip",
        dest="zip_codes",
        action="append",
        help="Limit run to specific ZIP codes (repeat or comma-separate).",
    )
    parser.add_argument(
        "--max-zips",
        type=int,
        help="Process at most this many ZIP codes (after filtering).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for orchestrator run artifacts.",
    )
    parser.add_argument(
        "--distance",
        type=int,
        default=100,
        help="Distance radius parameter sent with each request.",
    )
    parser.add_argument(
        "--category",
        default="both",
        help="Holosun category parameter submitted with each request.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--user-agent",
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/119.0.0.0 Safari/537.36"
        ),
        help="User-Agent header for Holosun requests.",
    )
    parser.add_argument(
        "--skip-raw",
        action="store_true",
        help="Skip writing per-ZIP raw artifacts (only write run summary).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging output.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Retry a failed ZIP this many times (in addition to the initial attempt).",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=5.0,
        help="Initial delay in seconds before retrying a ZIP after a failure.",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=2.0,
        help="Multiplier applied to the retry delay after each failed attempt.",
    )
    parser.add_argument(
        "--prompt-on-block",
        action="store_true",
        help="Prompt interactively when a ZIP is blocked to choose retry/skip/abort.",
    )
    parser.add_argument(
        "--flush-every",
        type=int,
        default=DEFAULT_FLUSH_EVERY,
        help=(
            "Flush normalized artifacts, refresh deliverable CSV, and persist metrics after this many "
            "processed ZIP codes (set to 0 to flush only at the end)."
        ),
    )
    parser.add_argument(
        "--deliverable-name",
        default=DEFAULT_DELIVERABLE_NAME,
        help="Filename (relative to run directory unless absolute) for the final deliverable CSV.",
    )
    parser.add_argument(
        "--metrics-name",
        help=(
            "Filename (relative to run directory unless absolute) for the metrics JSON. "
            "Defaults to <deliverable-name>.metrics.json."
        ),
    )
    parser.add_argument(
        "--list-delimiter",
        default=DEFAULT_LIST_DELIMITER,
        help="Delimiter used when flattening list fields into CSV output (default '|').",
    )
    return parser


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )


def log_stage(stage: str, message: str) -> None:
    LOGGER.info("[stage:%s] %s", stage, message)


def compute_retry_delay(base_delay: float, backoff: float, attempt_index: int) -> float:
    if attempt_index <= 0:
        return 0.0
    computed = base_delay * (backoff ** max(attempt_index - 1, 0))
    return max(computed, 0.0)


def prompt_block_action(zip_code: str, attempt: int, max_attempts: int) -> str:
    prompt = (
        f"ZIP {zip_code} was blocked (attempt {attempt}/{max_attempts}). "
        "Choose action: [r]etry / [s]kip / [a]bort > "
    )
    while True:
        try:
            decision = input(prompt)
        except EOFError:
            return "skip"
        if not decision:
            decision = "r"
        decision = decision.strip().lower()
        if decision in {"r", "retry"}:
            return "retry"
        if decision in {"s", "skip"}:
            return "skip"
        if decision in {"a", "abort"}:
            return "abort"
        print("Please enter 'r', 's', or 'a'.")


def expand_zip_list(zip_args: Optional[Sequence[str]], *, centroids: Dict[str, Dict[str, Any]]) -> List[str]:
    if not zip_args:
        return sorted(centroids.keys())
    selected: List[str] = []
    for entry in zip_args:
        for value in entry.split(","):
            code = value.strip()
            if not code:
                continue
            code = code.zfill(5)
            if code not in centroids:
                LOGGER.warning("ZIP %s not found in centroid CSV; skipping", code)
                continue
            selected.append(code)
    return sorted(dict.fromkeys(selected))


def append_manual_attention(run_dir: Path, zip_code: str, issues: str, payload: Dict[str, Any], *, response: Optional[requests.Response], body_text: Optional[str]) -> Path:
    blocked_dir = run_dir / "blocked_zips"
    blocked_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    artifact_path = blocked_dir / f"{timestamp}_{zip_code}_blocked.json"
    artifact = {
        "zip_code": zip_code,
        "issues": issues,
        "payload": payload,
        "status_code": response.status_code if response else None,
        "headers": dict(response.headers) if response else None,
        "body_snippet": (body_text or "")[:2000] if body_text else None,
        "detected_at": timestamp,
    }
    artifact_path.write_text(json.dumps(artifact, indent=2))

    DEFAULT_MANUAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_MANUAL_LOG.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "run_block": str(artifact_path),
                    "zip_code": zip_code,
                    "issues": issues,
                    "timestamp": timestamp,
                }
            )
            + "\n"
        )
    return artifact_path


def run_orchestrator(args: argparse.Namespace) -> int:
    configure_logging(args.verbose)

    run_started = datetime.utcnow()
    run_id = run_started.strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    zip_output_dir = run_dir / "zip_runs"
    if not args.skip_raw:
        zip_output_dir.mkdir(parents=True, exist_ok=True)

    list_delimiter = args.list_delimiter or DEFAULT_LIST_DELIMITER
    flush_every = max(args.flush_every, 0) if args.flush_every is not None else 0

    deliverable_name = args.deliverable_name or DEFAULT_DELIVERABLE_NAME
    deliverable_path = Path(deliverable_name)
    if not deliverable_path.is_absolute():
        deliverable_path = run_dir / deliverable_path

    metrics_name = args.metrics_name or f"{Path(deliverable_name).stem}.metrics.json"
    metrics_path = Path(metrics_name)
    if not metrics_path.is_absolute():
        metrics_path = run_dir / metrics_path

    normalized_json_path = run_dir / NORMALIZED_JSON_NAME
    normalized_csv_path = run_dir / NORMALIZED_CSV_NAME
    run_state_path = run_dir / RUN_STATE_FILENAME

    log_stage(Stage.LOAD_ZIPS, f"Loading ZIP centroids from {args.zip_csv}")
    try:
        centroids = load_zip_centroids(args.zip_csv)
    except Exception as exc:
        LOGGER.error("Failed to load ZIP centroids: %s", exc)
        return 2

    target_zips = expand_zip_list(args.zip_codes, centroids=centroids)
    if args.max_zips is not None:
        target_zips = target_zips[: args.max_zips]
    if not target_zips:
        LOGGER.error("No ZIP codes selected for processing")
        return 2

    accumulator = DealerAccumulator()
    zip_summaries: List[Dict[str, Any]] = []
    blocked_events: List[Dict[str, Any]] = []
    error_events: List[Dict[str, Any]] = []

    prompt_enabled = args.prompt_on_block and sys.stdin.isatty()
    if args.prompt_on_block and not prompt_enabled:
        LOGGER.warning(
            "Prompt-on-block requested but stdin is not interactive; proceeding without prompts.")

    max_attempts = max(args.max_retries, 0) + 1
    base_delay = max(args.retry_delay, 0.0)
    retry_backoff = args.retry_backoff if args.retry_backoff > 0 else 1.0

    total_zips = len(target_zips)
    abort_requested = False

    run_state: Dict[str, Any] = {
        "run_id": run_id,
        "started_at": run_started.isoformat() + "Z",
        "completed_at": None,
        "zip_total": total_zips,
        "zip_processed": 0,
        "blocked_count": 0,
        "error_count": 0,
        "unique_dealers": 0,
        "zip_summaries": zip_summaries,
        "blocked_events": blocked_events,
        "error_events": error_events,
        "aborted": False,
        "retry_policy": {
            "max_retries": max_attempts - 1,
            "initial_delay_seconds": base_delay,
            "backoff_multiplier": retry_backoff,
            "prompt_on_block_requested": bool(args.prompt_on_block),
            "prompt_on_block_enabled": bool(prompt_enabled),
        },
        "last_flush_at": None,
        "artifacts": {
            "run_state": str(run_state_path),
            "normalized_json": str(normalized_json_path),
            "normalized_csv": str(normalized_csv_path),
            "deliverable_csv": str(deliverable_path),
            "metrics_json": str(metrics_path),
        },
    }

    def persist_progress(*, final: bool, completed_at: Optional[str] = None) -> None:
        """Write the latest dealer, deliverable, and metrics artifacts."""

        dealers_snapshot = accumulator.to_list()
        flush_timestamp = completed_at or datetime.utcnow().isoformat() + "Z"

        try:
            normalized_json_path.write_text(json.dumps(dealers_snapshot, indent=2))
        except Exception as exc:
            LOGGER.error("Failed to write normalized dealer JSON: %s", exc)

        try:
            export_dealers_to_csv(dealers_snapshot, normalized_csv_path, list_delimiter=list_delimiter)
        except Exception as exc:
            LOGGER.error("Failed to write normalized dealer CSV: %s", exc)

        validation_issues = validate_dealers(dealers_snapshot)
        for issue in validation_issues:
            LOGGER.warning("Validation issue: %s", issue)

        deliverable_rows = build_deliverable_rows(dealers_snapshot, list_delimiter=list_delimiter)
        try:
            export_dealers_to_csv(
                deliverable_rows,
                deliverable_path,
                fieldnames=DELIVERABLE_FIELDNAMES,
                list_delimiter=list_delimiter,
            )
            LOGGER.info(
                "Refreshed deliverable CSV at %s (%d dealers)",
                deliverable_path,
                len(deliverable_rows),
            )
        except Exception as exc:
            LOGGER.error("Failed to write deliverable CSV: %s", exc)

        try:
            metrics = compute_metrics(dealers_snapshot)
        except Exception as exc:
            LOGGER.error("Failed to compute metrics: %s", exc)
            metrics = {}
        else:
            LOGGER.info(
                "Metrics snapshot: total=%d, unique=%d, with_phone=%d",
                metrics.get("total_dealers", 0),
                metrics.get("unique_dealer_ids", 0),
                metrics.get("dealers_with_phone", 0),
            )

        try:
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(json.dumps(metrics, indent=2))
        except Exception as exc:
            LOGGER.error("Failed to write metrics JSON: %s", exc)

        run_state.update(
            {
                "zip_processed": len(zip_summaries),
                "blocked_count": len(blocked_events),
                "error_count": len(error_events),
                "unique_dealers": len(accumulator),
                "aborted": abort_requested,
                "last_flush_at": flush_timestamp,
            }
        )
        run_state["completed_at"] = completed_at if final else None

        persistable_state = dict(run_state)
        persistable_state["zip_summaries"] = zip_summaries
        persistable_state["blocked_events"] = blocked_events
        persistable_state["error_events"] = error_events

        try:
            run_state_path.write_text(json.dumps(persistable_state, indent=2))
        except Exception as exc:
            LOGGER.error("Failed to write run state: %s", exc)

        if final and completed_at:
            try:
                (run_dir / "run_summary.json").write_text(json.dumps(persistable_state, indent=2))
            except Exception as exc:
                LOGGER.error("Failed to write run summary: %s", exc)

    log_stage(Stage.FETCH, f"Processing {total_zips} ZIP codes")

    for idx, zip_code in enumerate(target_zips, start=1):
        if abort_requested:
            break

        centroid = centroids.get(zip_code)
        if not centroid:
            error_events.append({"zip_code": zip_code, "error": "Centroid missing"})
            LOGGER.error("ZIP %s missing from centroid mapping", zip_code)
            continue

        try:
            payload = prepare_payload(zip_code, centroid, args.distance, args.category)
        except Exception as exc:
            LOGGER.error("Failed to prepare payload for ZIP %s: %s", zip_code, exc)
            error_events.append({"zip_code": zip_code, "error": str(exc)})
            continue

        attempt = 1
        zip_success = False
        response: Optional[requests.Response] = None
        response_data: Optional[Dict[str, Any]] = None
        body_text: Optional[str] = None
        blocked_metadata: Optional[Dict[str, Any]] = None

        while attempt <= max_attempts:
            log_stage(
                Stage.FETCH,
                f"[{idx}/{total_zips}] ZIP {zip_code}: submitting request (attempt {attempt}/{max_attempts})",
            )
            try:
                response, body_text = perform_request(payload, timeout=args.timeout, user_agent=args.user_agent)
                issues = detect_anti_automation(response, body_text)
                if issues:
                    raise AntiAutomationError("; ".join(issues))
                response_data = response.json()
            except AntiAutomationError as exc:
                block_message = str(exc)
                LOGGER.warning(
                    "ZIP %s flagged as anti-automation (attempt %d/%d): %s",
                    zip_code,
                    attempt,
                    max_attempts,
                    block_message,
                )
                should_retry = attempt < max_attempts
                decision = "retry" if should_retry else "skip"
                if prompt_enabled and should_retry:
                    decision = prompt_block_action(zip_code, attempt, max_attempts)
                if decision == "retry" and should_retry:
                    delay = compute_retry_delay(base_delay, retry_backoff, attempt)
                    if delay > 0:
                        LOGGER.info("Waiting %.1fs before retrying ZIP %s after block", delay, zip_code)
                        time.sleep(delay)
                    attempt += 1
                    continue
                artifact_path = append_manual_attention(
                    run_dir,
                    zip_code,
                    block_message,
                    payload,
                    response=response,
                    body_text=body_text,
                )
                resolution = (
                    "abort"
                    if decision == "abort"
                    else "skipped"
                    if decision == "skip"
                    else "exhausted"
                )
                blocked_metadata = {
                    "zip_code": zip_code,
                    "issues": block_message,
                    "artifact": str(artifact_path),
                    "attempts": attempt,
                    "resolution": resolution,
                }
                if decision == "abort":
                    abort_requested = True
                break
            except Exception as exc:
                LOGGER.error(
                    "Request failed for ZIP %s (attempt %d/%d): %s",
                    zip_code,
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt >= max_attempts:
                    error_events.append({"zip_code": zip_code, "error": str(exc), "attempts": attempt})
                    break
                delay = compute_retry_delay(base_delay, retry_backoff, attempt)
                if delay > 0:
                    LOGGER.info("Waiting %.1fs before retrying ZIP %s after error", delay, zip_code)
                    time.sleep(delay)
                attempt += 1
                continue
            else:
                zip_success = True
                break

        if blocked_metadata:
            blocked_events.append(blocked_metadata)
        if abort_requested:
            break
        if not zip_success or response is None or response_data is None:
            continue

        dealers_raw: Iterable[Dict[str, Any]] = response_data.get("data", {}).get("list", []) or []
        normalized = [normalize_dealer(raw, zip_code) for raw in dealers_raw]
        observed_at = datetime.utcnow().isoformat() + "Z"

        log_stage(
            Stage.NORMALIZE,
            f"[{idx}/{total_zips}] ZIP {zip_code}: normalized {len(normalized)} dealers (attempt {attempt})",
        )
        total_count, new_count = accumulator.ingest(
            normalized,
            zip_code=zip_code,
            observed_at=observed_at,
            run_reference=run_id,
        )

        artifact_dir = None
        if not args.skip_raw:
            try:
                artifact_dir = write_artifacts(
                    zip_output_dir,
                    zip_code,
                    payload,
                    response,
                    response_data,
                    normalized,
                    centroid,
                )
            except Exception as exc:
                LOGGER.error("Failed to write artifacts for ZIP %s: %s", zip_code, exc)

        zip_summaries.append(
            {
                "zip_code": zip_code,
                "dealer_count": len(normalized),
                "new_unique_dealers": new_count,
                "total_dealers_seen": total_count,
                "artifact_path": str(artifact_dir) if artifact_dir else None,
                "observed_at": observed_at,
                "attempts": attempt,
            }
        )

        if flush_every and idx % flush_every == 0:
            log_stage(
                Stage.PERSIST,
                f"[{idx}/{total_zips}] Flushing artifacts and refreshing deliverables",
            )
            persist_progress(final=False)

    log_stage(Stage.PERSIST, "Persisting run outputs")
    run_completed = datetime.utcnow()
    run_state["aborted"] = abort_requested
    completed_at = run_completed.isoformat() + "Z"
    persist_progress(final=True, completed_at=completed_at)

    status_word = "aborted" if abort_requested else "complete"
    LOGGER.info(
        "Run %s %s: %d ZIPs processed, %d unique dealers, %d blocked, %d errors",
        run_id,
        status_word,
        len(zip_summaries),
        len(accumulator),
        len(blocked_events),
        len(error_events),
    )
    return 4 if abort_requested else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run_orchestrator(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
