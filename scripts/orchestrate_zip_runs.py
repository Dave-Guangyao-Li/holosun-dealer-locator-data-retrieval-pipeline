"""Stage-aware orchestrator for iterating California ZIP codes.

The orchestrator coordinates multi-ZIP executions against the Holosun dealer
locator using offline centroids. It provides stage progress updates, performs
anti-automation checks, accumulates normalized dealer records with deduplication,
and persists both raw artifacts and run summaries under
``data/raw/orchestrator_runs/<timestamp>/``.
"""
from __future__ import annotations

import argparse
import os
import sys
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.fetch_single_zip import (
    AntiAutomationError,
    detect_anti_automation,
    load_zip_centroids,
    normalize_dealer,
    perform_request,
    prepare_payload,
    write_artifacts,
)

LOGGER = logging.getLogger("holosun.orchestrator")
CITY_STATE_ZIP_RE = re.compile(r"^(?P<city>.*?)(?:,\s*(?P<state>[A-Z]{2}))?\s+(?P<postal>\d{5})(?:-\d{4})?$")
DEFAULT_OUTPUT_DIR = Path("data/raw/orchestrator_runs")
DEFAULT_MANUAL_LOG = Path("logs/manual_attention.log")


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
    return parser


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )


def log_stage(stage: str, message: str) -> None:
    LOGGER.info("[stage:%s] %s", stage, message)


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

    total_zips = len(target_zips)
    log_stage(Stage.FETCH, f"Processing {total_zips} ZIP codes")

    for idx, zip_code in enumerate(target_zips, start=1):
        centroid = centroids.get(zip_code)
        if not centroid:
            error_events.append({"zip_code": zip_code, "error": "Centroid missing"})
            LOGGER.error("ZIP %s missing from centroid mapping", zip_code)
            continue

        log_stage(Stage.FETCH, f"[{idx}/{total_zips}] ZIP {zip_code}: submitting request")
        try:
            payload = prepare_payload(zip_code, centroid, args.distance, args.category)
        except Exception as exc:
            LOGGER.error("Failed to prepare payload for ZIP %s: %s", zip_code, exc)
            error_events.append({"zip_code": zip_code, "error": str(exc)})
            continue

        response = None
        body_text = None
        try:
            response, body_text = perform_request(payload, timeout=args.timeout, user_agent=args.user_agent)
            issues = detect_anti_automation(response, body_text)
            if issues:
                raise AntiAutomationError("; ".join(issues))
            response_data = response.json()
        except AntiAutomationError as exc:
            LOGGER.warning("ZIP %s flagged as anti-automation: %s", zip_code, exc)
            artifact_path = append_manual_attention(run_dir, zip_code, str(exc), payload, response=response, body_text=body_text)
            blocked_events.append({"zip_code": zip_code, "issues": str(exc), "artifact": str(artifact_path)})
            continue
        except Exception as exc:
            LOGGER.error("Request failed for ZIP %s: %s", zip_code, exc)
            error_events.append({"zip_code": zip_code, "error": str(exc)})
            continue

        dealers_raw: Iterable[Dict[str, Any]] = response_data.get("data", {}).get("list", []) or []
        normalized = [normalize_dealer(raw, zip_code) for raw in dealers_raw]
        observed_at = datetime.utcnow().isoformat() + "Z"

        log_stage(Stage.NORMALIZE, f"[{idx}/{total_zips}] ZIP {zip_code}: normalized {len(normalized)} dealers")
        total_count, new_count = accumulator.ingest(normalized, zip_code=zip_code, observed_at=observed_at, run_reference=run_id)

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
            }
        )

    log_stage(Stage.PERSIST, "Writing run summary")
    run_completed = datetime.utcnow()
    run_summary = {
        "run_id": run_id,
        "started_at": run_started.isoformat() + "Z",
        "completed_at": run_completed.isoformat() + "Z",
        "zip_total": total_zips,
        "zip_processed": len(zip_summaries),
        "blocked_count": len(blocked_events),
        "error_count": len(error_events),
        "unique_dealers": len(accumulator),
        "zip_summaries": zip_summaries,
        "blocked_events": blocked_events,
        "error_events": error_events,
    }
    (run_dir / "run_summary.json").write_text(json.dumps(run_summary, indent=2))
    (run_dir / "normalized_dealers.json").write_text(json.dumps(accumulator.to_list(), indent=2))

    LOGGER.info(
        "Run %s complete: %d ZIPs processed, %d unique dealers, %d blocked, %d errors",
        run_id,
        len(zip_summaries),
        len(accumulator),
        len(blocked_events),
        len(error_events),
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run_orchestrator(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
