from __future__ import annotations

import csv
from pathlib import Path

from holosun_locator.exports import (
    CSV_FIELDS,
    compute_metrics,
    export_dealers_to_csv,
    validate_dealers,
)


SAMPLE_DEALERS = [
    {
        "dealer_id": "dealer-001",
        "dealer_name": "Alpha Optics",
        "street": "123 Main St",
        "city": "Los Angeles",
        "state": "CA",
        "postal_code": "90001",
        "phone": "555-0001",
        "website": "https://alpha.example.com",
        "latitude": 34.0,
        "longitude": -118.0,
        "address_text": "123 Main St, Los Angeles, CA 90001",
        "address_lines": ["123 Main St", "Los Angeles, CA 90001"],
        "emails": ["sales@alpha.example.com"],
        "source_zips": ["90001", "90002"],
        "holosun_ids": [101],
        "runs": ["20251008T122746Z"],
        "first_seen_at": "2025-10-08T12:27:52Z",
        "last_seen_at": "2025-10-08T12:27:52Z",
    },
    {
        "dealer_id": "dealer-002",
        "dealer_name": "Beta Tactical",
        "street": "500 Market Ave",
        "city": "San Diego",
        "state": "CA",
        "postal_code": "92101",
        "phone": "",
        "website": None,
        "latitude": None,
        "longitude": None,
        "address_text": "500 Market Ave, San Diego, CA 92101",
        "address_lines": ["500 Market Ave", "San Diego, CA 92101"],
        "emails": [],
        "source_zips": ["92101"],
        "holosun_ids": [],
        "runs": ["20251008T122800Z"],
        "first_seen_at": "2025-10-08T12:28:00Z",
        "last_seen_at": "2025-10-08T12:28:00Z",
    },
]


def test_validate_dealers_flags_missing_fields():
    issues = validate_dealers(SAMPLE_DEALERS)
    assert issues == [], f"Unexpected validation issues: {issues}"

    broken = [{"dealer_name": "Missing everything"}]
    broken_issues = validate_dealers(broken)
    assert any("dealer_id" in issue for issue in broken_issues)
    assert any("source_zips" in issue for issue in broken_issues)


def test_export_dealers_to_csv_writes_expected_columns(tmp_path: Path):
    output_file = tmp_path / "dealers.csv"
    export_dealers_to_csv(SAMPLE_DEALERS, output_file, list_delimiter=";")

    assert output_file.exists()

    with output_file.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(CSV_FIELDS)
        rows = list(reader)

    assert len(rows) == 2
    first_row = rows[0]
    assert first_row["emails"] == "sales@alpha.example.com"
    assert first_row["source_zips"] == "90001;90002"
    assert rows[1]["phone"] == ""


def test_compute_metrics_returns_spot_checks():
    metrics = compute_metrics(SAMPLE_DEALERS)
    assert metrics["total_dealers"] == 2
    assert metrics["unique_dealer_ids"] == 2
    assert metrics["dealers_with_phone"] == 1
    assert metrics["dealers_missing_geocode"] == 1
    assert metrics["unique_source_zips"] == 3
    assert metrics["average_source_zips"] == 1.5
