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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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
CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<city>.*?)(?:,\s*(?P<state>[A-Z]{2})|\s+(?P<state_alt>[A-Z]{2}))?\s+(?P<postal>\d{5})(?:-\d{4})?$"
)
STATE_POSTAL_REGEX = re.compile(r"(?P<state>[A-Z]{2})\s+(?P<postal>\d{5})(?:-\d{4})?$")
POSTAL_REGEX = re.compile(r"(\d{5})(?:-\d{4})?$")
STREET_SUFFIX_TOKENS = {
    "st",
    "st.",
    "street",
    "ave",
    "ave.",
    "avenue",
    "blvd",
    "blvd.",
    "boulevard",
    "rd",
    "rd.",
    "road",
    "dr",
    "dr.",
    "drive",
    "way",
    "ln",
    "ln.",
    "lane",
    "pl",
    "pl.",
    "place",
    "pkwy",
    "pkwy.",
    "parkway",
    "hwy",
    "hwy.",
    "highway",
    "ste",
    "ste.",
    "suite",
    "unit",
    "apt",
    "apt.",
    "bldg",
    "ctr",
    "center",
    "sq",
    "sq.",
}
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
    "address",
    "phone",
    "website",
)


def normalize_postal(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"\d{5}", str(value))
    if not match:
        return None
    return match.group(0)


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
            postal_code=normalize_postal(postal),
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
            self.postal_code = normalize_postal(postal)
        elif postal and self.postal_code and self.postal_code == normalize_postal(normalized.get("source_zip")):
            self.postal_code = normalize_postal(postal)
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

    @classmethod
    def from_snapshot(cls, snapshot: Dict[str, Any]) -> "DealerAggregate":
        dealer_id = snapshot.get("dealer_id")
        if not dealer_id:
            raise ValueError("Snapshot missing dealer_id")

        def _list(value: Any) -> List[Any]:
            if not value:
                return []
            if isinstance(value, list):
                return list(value)
            return [value]

        def _string_list(value: Any) -> List[str]:
            result: List[str] = []
            for entry in _list(value):
                if entry is None:
                    continue
                text = str(entry).strip()
                if text and text not in result:
                    result.append(text)
            return result

        def _unique_list(value: Any) -> List[Any]:
            result: List[Any] = []
            for entry in _list(value):
                if entry not in result:
                    result.append(entry)
            return result

        return cls(
            dealer_id=dealer_id,
            dealer_name=snapshot.get("dealer_name"),
            street=snapshot.get("street"),
            city=snapshot.get("city"),
            state=snapshot.get("state"),
            postal_code=normalize_postal(snapshot.get("postal_code")),
            phone=snapshot.get("phone"),
            website=snapshot.get("website"),
            latitude=snapshot.get("latitude"),
            longitude=snapshot.get("longitude"),
            address_text=snapshot.get("address_text"),
            address_lines=_string_list(snapshot.get("address_lines")),
            emails=_string_list(snapshot.get("emails")),
            source_zips=_string_list(snapshot.get("source_zips")),
            holosun_ids=_unique_list(snapshot.get("holosun_ids")),
            runs=_string_list(snapshot.get("runs")),
            first_seen_at=snapshot.get("first_seen_at")
            or snapshot.get("last_seen_at")
            or datetime.utcnow().isoformat() + "Z",
            last_seen_at=snapshot.get("last_seen_at")
            or snapshot.get("first_seen_at")
            or datetime.utcnow().isoformat() + "Z",
        )

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
    lines = [line.strip() for line in (normalized.get("address_lines") or []) if line and line.strip()]
    address_text = (normalized.get("address_text") or "").strip()

    street: Optional[str] = lines[0] if lines else None
    city: Optional[str] = None
    state: Optional[str] = None
    postal: Optional[str] = None

    for line in lines[1:]:
        match = CITY_STATE_ZIP_RE.match(line.strip())
        if match:
            city = (match.group("city") or "").strip() or city
            state_candidate = match.group("state") or match.group("state_alt")
            if state_candidate:
                state = state or state_candidate
            postal = postal or match.group("postal")
            break

    def _apply_from_text(text: str) -> None:
        nonlocal street, city, state, postal
        if not text:
            return

        state_zip_match = STATE_POSTAL_REGEX.search(text)
        if state_zip_match:
            state = state or state_zip_match.group("state")
            postal = postal or state_zip_match.group("postal")
            prefix = text[: state_zip_match.start()].rstrip(", ")
        else:
            prefix = text

        if "," in prefix:
            parts = [part.strip() for part in prefix.split(",") if part.strip()]
            if parts:
                city_candidate = parts[-1]
                street_candidate = ", ".join(parts[:-1]).strip() if len(parts) > 1 else None
                if len(parts) == 1:
                    tokens = city_candidate.split()
                    city_tokens: List[str] = []
                    while tokens:
                        candidate = tokens[-1]
                        candidate_lower = candidate.rstrip(".,#").lower()
                        if any(ch.isdigit() for ch in candidate) or candidate_lower in STREET_SUFFIX_TOKENS:
                            break
                        city_tokens.insert(0, tokens.pop())
                        if len(city_tokens) >= 3:
                            break
                    if city_tokens:
                        city_candidate = " ".join(city_tokens)
                        street_candidate = " ".join(tokens)
                city_candidate = city_candidate.strip()
                if city_candidate:
                    city = city or city_candidate
                if street_candidate:
                    street_candidate = street_candidate.strip()
                    if street_candidate:
                        if not street or (lines and street == lines[0]):
                            street = re.sub(r"\s{2,}", " ", street_candidate).strip(" ,")
        else:
            tokens = prefix.split()
            if tokens:
                city_tokens: List[str] = []
                while tokens:
                    candidate = tokens[-1]
                    candidate_lower = candidate.rstrip(".,#").lower()
                    if any(ch.isdigit() for ch in candidate) or candidate.endswith(('.', '#')) or candidate_lower in STREET_SUFFIX_TOKENS:
                        break
                    city_tokens.insert(0, tokens.pop())
                    if len(city_tokens) >= 3:
                        break
                if city_tokens:
                    city = city or " ".join(city_tokens).strip()
                street_candidate = " ".join(tokens).strip()
                if street_candidate:
                    if not street or (lines and street == lines[0]):
                        street = re.sub(r"\s{2,}", " ", street_candidate).strip(" ,")

        if not postal:
            zip_match = POSTAL_REGEX.search(text)
            if zip_match:
                postal = postal or zip_match.group(1)

    _apply_from_text(address_text)

    if lines and not street:
        street = lines[0]

    postal = normalize_postal(postal or normalized.get("record_zip") or normalized.get("source_zip"))
    state = state or (state_zip_match.group("state") if (state_zip_match := STATE_POSTAL_REGEX.search(address_text)) else None)

    return (street.strip() if street else None), (city.strip() if city else None), state, postal


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

    def load_snapshot(self, records: Iterable[Dict[str, Any]]) -> None:
        for record in records:
            try:
                aggregate = DealerAggregate.from_snapshot(record)
            except ValueError:
                continue
            self._records[aggregate.dealer_id] = aggregate


@dataclass
class ResumeState:
    run_id: Optional[str]
    run_dir: Path
    run_state_path: Path
    processed_zips: Set[str]
    blocked_zips: Set[str]
    dealers_snapshot: List[Dict[str, Any]]
    normalized_json_path: Optional[Path]
    manual_log_path: Optional[Path]

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "run_state": str(self.run_state_path),
            "normalized_json": str(self.normalized_json_path) if self.normalized_json_path else None,
            "manual_log": str(self.manual_log_path) if self.manual_log_path else None,
            "processed_zips": sorted(self.processed_zips),
            "blocked_zips": sorted(self.blocked_zips),
        }


def build_deliverable_rows(
    dealers: Sequence[Dict[str, Any]],
    *,
    list_delimiter: str,
) -> List[Dict[str, Any]]:
    """Transform normalized dealers into the trimmed deliverable schema."""

    rows: List[Dict[str, Any]] = []
    for dealer in dealers:
        address_parts: List[str] = []
        street = dealer.get("street")
        city = dealer.get("city")
        state = dealer.get("state")
        postal = dealer.get("postal_code")

        if street:
            address_parts.append(str(street))

        locality_parts = [part for part in [city, state] if part]
        if postal:
            locality = ", ".join(locality_parts) if locality_parts else ""
            if locality:
                locality = f"{locality} {postal}"
            else:
                locality = postal
            address_parts.append(locality)
        elif locality_parts:
            address_parts.append(", ".join(locality_parts))

        address_display = ", ".join(part for part in address_parts if part)

        rows.append(
            {
                "dealer_name": dealer.get("dealer_name") or "",
                "address": address_display,
                "phone": dealer.get("phone") or "",
                "website": dealer.get("website") or "",
            }
        )
    return rows


def compute_dealer_id(dealer: Dict[str, Any]) -> str:
    street, city, _, postal = extract_address_components(dealer)
    parts = [
        (dealer.get("dealer_name") or "").strip().lower(),
        (street or "").strip().lower(),
        (city or "").strip().lower(),
        (normalize_postal(postal) or "").strip(),
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
    parser.add_argument(
        "--resume-state",
        type=Path,
        help="Path to a previous run directory or run_state.json used to seed resume data.",
    )
    parser.add_argument(
        "--resume-policy",
        choices=["skip", "blocked", "all"],
        default="skip",
        help=(
            "How to handle ZIP selection when resuming."
            " 'skip' removes ZIPs already completed in the resume state."
            " 'blocked' limits the run to blocked ZIPs."
            " 'all' leaves the requested ZIP list unchanged."
        ),
    )
    parser.add_argument(
        "--include-manual-log",
        action="store_true",
        help="Include ZIP codes recorded in the manual attention log when resuming.",
    )
    parser.add_argument(
        "--manual-log",
        type=Path,
        default=DEFAULT_MANUAL_LOG,
        help="Path to manual attention log (used with --include-manual-log).",
    )
    parser.add_argument(
        "--manual-log-run",
        help="Limit manual attention log replay to entries whose artifact path contains this run ID.",
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


def append_manual_attention(
    run_dir: Path,
    zip_code: str,
    issues: str,
    payload: Dict[str, Any],
    *,
    response: Optional[requests.Response],
    body_text: Optional[str],
    manual_log_path: Path = DEFAULT_MANUAL_LOG,
) -> Path:
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

    manual_log_path = manual_log_path.expanduser()
    manual_log_path.parent.mkdir(parents=True, exist_ok=True)
    with manual_log_path.open("a", encoding="utf-8") as handle:
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


def resolve_artifact_path(base_dir: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def load_resume_state(resume_path: Path) -> ResumeState:
    resolved = resume_path.expanduser().resolve()
    run_state_path = resolved / RUN_STATE_FILENAME if resolved.is_dir() else resolved
    if not run_state_path.exists():
        raise FileNotFoundError(f"Resume state not found at {run_state_path}")

    data = json.loads(run_state_path.read_text())
    run_dir = run_state_path.parent

    processed_zips = {
        str(entry.get("zip_code")).zfill(5)
        for entry in data.get("zip_summaries", [])
        if entry and entry.get("zip_code")
    }
    blocked_zips = {
        str(entry.get("zip_code")).zfill(5)
        for entry in data.get("blocked_events", [])
        if entry and entry.get("zip_code")
    }

    artifacts = data.get("artifacts") or {}
    normalized_json_path = resolve_artifact_path(run_dir, artifacts.get("normalized_json"))

    dealers_snapshot: List[Dict[str, Any]] = []
    if normalized_json_path and normalized_json_path.exists():
        try:
            dealers_snapshot = json.loads(normalized_json_path.read_text())
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.warning("Failed to load dealers snapshot from %s: %s", normalized_json_path, exc)

    manual_log_entry = artifacts.get("manual_log") or data.get("manual_log")
    manual_log_path = resolve_artifact_path(run_dir, manual_log_entry) if manual_log_entry else DEFAULT_MANUAL_LOG

    return ResumeState(
        run_id=data.get("run_id"),
        run_dir=run_dir,
        run_state_path=run_state_path,
        processed_zips=set(processed_zips),
        blocked_zips=set(blocked_zips),
        dealers_snapshot=dealers_snapshot,
        normalized_json_path=normalized_json_path,
        manual_log_path=manual_log_path,
    )


def load_manual_attention_zips(log_path: Path, *, run_id_filter: Optional[str] = None) -> List[str]:
    if not log_path.exists():
        return []

    zips: List[str] = []
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            artifact_ref = str(payload.get("run_block") or "")
            if run_id_filter and run_id_filter not in artifact_ref:
                continue
            zip_value = str(payload.get("zip_code") or "").strip()
            if not zip_value:
                continue
            zips.append(zip_value.zfill(5))
    return sorted(dict.fromkeys(zips))


def run_orchestrator(args: argparse.Namespace) -> int:
    configure_logging(args.verbose)

    resume_state: Optional[ResumeState] = None
    if args.resume_state:
        try:
            resume_state = load_resume_state(args.resume_state)
        except FileNotFoundError as exc:
            LOGGER.error("%s", exc)
            return 3
        except Exception as exc:
            LOGGER.error("Failed to load resume state: %s", exc)
            return 3
        else:
            LOGGER.info(
                "Loaded resume state from %s (processed=%d, blocked=%d)",
                resume_state.run_state_path,
                len(resume_state.processed_zips),
                len(resume_state.blocked_zips),
            )

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

    manual_log_path = args.manual_log
    if resume_state and manual_log_path == DEFAULT_MANUAL_LOG and resume_state.manual_log_path:
        manual_log_path = resume_state.manual_log_path

    manual_run_filter = args.manual_log_run or (resume_state.run_id if resume_state and not args.manual_log_run else None)
    manual_log_zips: Set[str] = set()
    manual_log_used: Set[str] = set()
    if args.include_manual_log:
        raw_manual_zips = set(load_manual_attention_zips(manual_log_path, run_id_filter=manual_run_filter))
        unknown_manual = sorted(zip_code for zip_code in raw_manual_zips if zip_code not in centroids)
        if unknown_manual:
            LOGGER.warning(
                "Manual attention log contained %d ZIPs not present in centroid table; skipping", len(unknown_manual)
            )
        manual_log_zips = {zip_code for zip_code in raw_manual_zips if zip_code in centroids}
        manual_log_used = set(manual_log_zips)
        LOGGER.info(
            "Loaded %d ZIPs from manual attention log %s",
            len(manual_log_zips),
            manual_log_path,
        )

    resume_processed = resume_state.processed_zips if resume_state else set()
    resume_blocked = resume_state.blocked_zips if resume_state else set()

    if resume_state and args.resume_policy == "skip" and resume_processed:
        before = len(target_zips)
        target_zips = [zip_code for zip_code in target_zips if zip_code not in resume_processed]
        skipped = before - len(target_zips)
        if skipped:
            LOGGER.info("Skipping %d ZIPs already completed in resume state", skipped)

    if args.resume_policy == "blocked":
        blocked_targets: Set[str] = set(resume_blocked)
        if args.include_manual_log:
            blocked_targets |= manual_log_zips
        if not blocked_targets:
            LOGGER.error("Resume policy 'blocked' selected but no blocked ZIPs were found")
            return 3
        target_zips = sorted(blocked_targets)
        manual_log_zips = set()
    else:
        if args.include_manual_log and manual_log_zips:
            merged = list(target_zips) + list(manual_log_zips)
            target_zips = sorted(dict.fromkeys(merged))

    if not target_zips and not resume_state:
        LOGGER.error("No ZIP codes selected for processing")
        return 2

    accumulator = DealerAccumulator()
    if resume_state and resume_state.dealers_snapshot:
        accumulator.load_snapshot(resume_state.dealers_snapshot)
        LOGGER.info("Seeded accumulator with %d dealers from resume snapshot", len(accumulator))

    initial_unique_dealers = len(accumulator)

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
    resume_refresh_only = bool(resume_state and total_zips == 0)
    abort_requested = False

    if resume_refresh_only:
        LOGGER.info("No ZIPs remain after applying resume policy; will refresh artifacts and exit once persistence completes")

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
            "manual_log": str(manual_log_path) if (args.include_manual_log or resume_state) else None,
        },
        "resume_policy": args.resume_policy,
        "resume_source": resume_state.to_metadata() if resume_state else None,
        "manual_log_enabled": bool(args.include_manual_log),
        "manual_log_path": str(manual_log_path) if (args.include_manual_log or resume_state) else None,
        "manual_log_run_filter": manual_run_filter,
        "manual_log_zips": sorted(manual_log_used) if manual_log_used else [],
        "preexisting_zip_total": len(resume_processed),
        "preexisting_blocked_total": len(resume_blocked),
        "initial_unique_dealers": initial_unique_dealers,
        "resume_refresh_only": resume_refresh_only,
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
                    manual_log_path=manual_log_path,
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


def normalize_postal(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"\d{5}", str(value))
    if not match:
        return None
    return match.group(0)
