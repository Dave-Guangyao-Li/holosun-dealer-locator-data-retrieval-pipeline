"""Utilities for validating and exporting normalized dealer records."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Sequence


CSV_FIELDS: Sequence[str] = (
    "dealer_id",
    "dealer_name",
    "street",
    "city",
    "state",
    "postal_code",
    "phone",
    "website",
    "latitude",
    "longitude",
    "address_text",
    "address_lines",
    "emails",
    "source_zips",
    "holosun_ids",
    "runs",
    "first_seen_at",
    "last_seen_at",
)

LIST_FIELDS = {
    "address_lines",
    "emails",
    "source_zips",
    "holosun_ids",
    "runs",
}


def load_normalized_dealers(path: Path) -> List[Dict[str, Any]]:
    """Load the normalized dealer JSON produced by the orchestrator."""

    if not path.exists():
        raise FileNotFoundError(f"Normalized dealer JSON not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Normalized dealer payload must be a list")
    result: List[Dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("Dealer entries must be JSON objects")
        result.append(entry)
    return result


def validate_dealers(dealers: Sequence[Dict[str, Any]]) -> List[str]:
    """Return a collection of validation issues for the dealer payload."""

    errors: List[str] = []
    seen_ids = set()
    required_fields = {"dealer_id", "dealer_name", "source_zips", "runs", "first_seen_at", "last_seen_at"}

    for idx, dealer in enumerate(dealers):
        context = f"dealer[{idx}]"
        missing = [field for field in required_fields if field not in dealer]
        for field in missing:
            errors.append(f"{context}: missing required field '{field}'")

        dealer_id = dealer.get("dealer_id")
        if not isinstance(dealer_id, str) or not dealer_id:
            errors.append(f"{context}: dealer_id must be a non-empty string")
        elif dealer_id in seen_ids:
            errors.append(f"{context}: duplicate dealer_id '{dealer_id}'")
        else:
            seen_ids.add(dealer_id)

        source_zips = dealer.get("source_zips", [])
        if not isinstance(source_zips, list) or not all(isinstance(zip_code, str) for zip_code in source_zips):
            errors.append(f"{context}: source_zips must be a list of strings")
        elif not source_zips:
            errors.append(f"{context}: source_zips must not be empty")

        runs = dealer.get("runs", [])
        if not isinstance(runs, list) or not all(isinstance(run_id, str) for run_id in runs):
            errors.append(f"{context}: runs must be a list of strings")
        elif not runs:
            errors.append(f"{context}: runs must not be empty")

        for temporal_field in ("first_seen_at", "last_seen_at"):
            if temporal_field in dealer and not isinstance(dealer.get(temporal_field), str):
                errors.append(f"{context}: {temporal_field} must be an ISO string")

        if dealer.get("emails") and (
            not isinstance(dealer["emails"], list)
            or not all(isinstance(email, str) for email in dealer["emails"])
        ):
            errors.append(f"{context}: emails must be a list of strings when present")

        for geo_field in ("latitude", "longitude"):
            value = dealer.get(geo_field)
            if value is not None and not isinstance(value, (int, float)):
                errors.append(f"{context}: {geo_field} must be numeric or null")

    return errors


def compute_metrics(dealers: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute spot-check metrics covering coverage and data completeness."""

    total = len(dealers)
    if total == 0:
        return {
            "total_dealers": 0,
            "unique_dealer_ids": 0,
            "duplicate_dealer_ids": 0,
            "dealers_with_phone": 0,
            "dealers_with_email": 0,
            "dealers_missing_geocode": 0,
            "average_source_zips": 0.0,
            "max_source_zips": 0,
            "unique_source_zips": 0,
            "unique_runs": 0,
        }

    unique_ids = {dealer.get("dealer_id") for dealer in dealers if dealer.get("dealer_id")}
    dealers_with_phone = sum(1 for dealer in dealers if dealer.get("phone"))
    dealers_with_email = sum(1 for dealer in dealers if dealer.get("emails"))
    dealers_missing_geocode = sum(
        1 for dealer in dealers if not dealer.get("latitude") or not dealer.get("longitude")
    )
    source_zip_counts = [len(dealer.get("source_zips") or []) for dealer in dealers]
    unique_source_zips = {zip_code for dealer in dealers for zip_code in (dealer.get("source_zips") or [])}
    unique_runs = {run_id for dealer in dealers for run_id in (dealer.get("runs") or [])}

    return {
        "total_dealers": total,
        "unique_dealer_ids": len(unique_ids),
        "duplicate_dealer_ids": total - len(unique_ids),
        "dealers_with_phone": dealers_with_phone,
        "dealers_missing_phone": total - dealers_with_phone,
        "dealers_with_email": dealers_with_email,
        "dealers_missing_email": total - dealers_with_email,
        "dealers_missing_geocode": dealers_missing_geocode,
        "average_source_zips": round(mean(source_zip_counts), 2) if source_zip_counts else 0.0,
        "max_source_zips": max(source_zip_counts) if source_zip_counts else 0,
        "unique_source_zips": len(unique_source_zips),
        "unique_runs": len(unique_runs),
    }


def export_dealers_to_csv(
    dealers: Sequence[Dict[str, Any]],
    output_path: Path,
    *,
    fieldnames: Sequence[str] = CSV_FIELDS,
    list_delimiter: str = "|",
) -> None:
    """Serialize normalized dealers to CSV."""

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        for dealer in dealers:
            row: Dict[str, Any] = {}
            for field in fieldnames:
                value = dealer.get(field)
                if field in LIST_FIELDS:
                    if not value:
                        row[field] = ""
                    elif isinstance(value, (list, tuple)):
                        row[field] = list_delimiter.join(str(item) for item in value if item is not None)
                    else:
                        row[field] = str(value)
                elif value is None:
                    row[field] = ""
                else:
                    row[field] = value
            writer.writerow(row)
