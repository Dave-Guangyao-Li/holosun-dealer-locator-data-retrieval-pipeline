"""Download and validate California ZIP code reference data.

This utility downloads a public state/county/ZIP lookup CSV from GitHub,
filters for California records, removes non-standard ZIPs, and writes both a
cleaned CSV plus a metadata JSON describing the retrieval. The resulting
artifacts feed the broader Holosun dealer pipeline.

Example:
    poetry run python scripts/fetch_ca_zip_codes.py \
        --output-csv data/processed/ca_zip_codes.csv \
        --metadata-json data/processed/ca_zip_codes.metadata.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List

import requests

LOGGER = logging.getLogger("holosun.zipcodes")
DEFAULT_SOURCE_URL = "https://raw.githubusercontent.com/scpike/us-state-county-zip/master/geo-data.csv"
DEFAULT_CENTROID_URL = "https://raw.githubusercontent.com/OpenDataDE/State-zip-code-GeoJSON/master/ca_california_zip_codes_geo.min.json"
DEFAULT_FALLBACK_CENTROID_URL = "https://raw.githubusercontent.com/millbj92/US-Zip-Codes-JSON/master/USCities.json"

REQUIRED_FIELDS = ["zip", "city", "state", "county"]
OUTPUT_HEADERS = ["zip", "city", "state", "county", "latitude", "longitude"]

MANUAL_CENTROID_OVERRIDES: Dict[str, Dict[str, float]] = {
    # ZIP 91719 (Corona) is a non-ZCTA code missing from both the CA GeoJSON and
    # fallback USCities datasets; coordinates sourced from OSM Nominatim lookup
    # on 2025-10-08 to anchor requests near the associated community.
    "91719": {"latitude": 33.8632021, "longitude": -117.5352347},
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/processed/ca_zip_codes.csv"),
        help="CSV file path for the processed ZIP codes.",
    )
    parser.add_argument(
        "--metadata-json",
        type=Path,
        default=Path("data/processed/ca_zip_codes.metadata.json"),
        help="Path to the metadata JSON artifact.",
    )
    parser.add_argument(
        "--source-url",
        default=DEFAULT_SOURCE_URL,
        help="URL to the raw ZIP dataset CSV.",
    )
    parser.add_argument(
        "--centroid-url",
        default=DEFAULT_CENTROID_URL,
        help="URL to the California ZIP centroid GeoJSON dataset.",
    )
    parser.add_argument(
        "--fallback-centroid-url",
        default=DEFAULT_FALLBACK_CENTROID_URL,
        help="URL to the nationwide ZIP centroid JSON used as a fallback for non-ZCTA ZIPs.",
    )
    parser.add_argument(
        "--min-expected",
        type=int,
        default=1500,
        help="Minimum acceptable count of California ZIP codes before aborting.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP request timeout in seconds.",
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


def fetch_dataset(source_url: str, timeout: int) -> str:
    LOGGER.info("Downloading dataset from %s", source_url)
    response = requests.get(source_url, timeout=timeout)
    response.raise_for_status()
    LOGGER.info("Downloaded %d bytes", len(response.content))
    return response.text


def fetch_centroids(centroid_url: str, timeout: int) -> Dict[str, Dict[str, float]]:
    LOGGER.info("Downloading centroid dataset from %s", centroid_url)
    response = requests.get(centroid_url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    mapping: Dict[str, Dict[str, float]] = {}
    features = payload.get("features", [])
    for feature in features:
        props = feature.get("properties", {})
        zip_code = str(props.get("ZCTA5CE10", "")).strip()
        lat_raw = props.get("INTPTLAT10")
        lon_raw = props.get("INTPTLON10")
        if not zip_code or lat_raw is None or lon_raw is None:
            continue
        try:
            lat = float(str(lat_raw))
            lon = float(str(lon_raw))
        except (TypeError, ValueError):
            LOGGER.debug("Skipping centroid with invalid coordinates: %s", zip_code)
            continue
        mapping[zip_code.zfill(5)] = {"latitude": lat, "longitude": lon}
    LOGGER.info("Parsed %d centroid entries", len(mapping))
    return mapping


def fetch_fallback_centroids(fallback_url: str, timeout: int) -> Dict[str, Dict[str, float]]:
    LOGGER.info("Downloading fallback centroid dataset from %s", fallback_url)
    response = requests.get(fallback_url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    mapping: Dict[str, Dict[str, float]] = {}
    for entry in payload:
        zip_value = entry.get("zip_code")
        lat = entry.get("latitude")
        lon = entry.get("longitude")
        if zip_value is None or lat is None or lon is None:
            continue
        zip_str = str(zip_value).zfill(5)
        try:
            mapping[zip_str] = {"latitude": float(lat), "longitude": float(lon)}
        except (TypeError, ValueError):
            continue
    LOGGER.info("Parsed %d fallback centroid entries", len(mapping))
    return mapping


def parse_records(raw_csv: str) -> Iterable[Dict[str, str]]:
    reader = csv.DictReader(io.StringIO(raw_csv))
    for row in reader:
        yield row


def filter_california(records: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    filtered = [row for row in records if row.get("state_abbr") == "CA"]
    LOGGER.info("Filtered down to %d California rows", len(filtered))
    return filtered


def is_valid_zip(zip_code: str) -> bool:
    return zip_code.isdigit() and len(zip_code) == 5


def transform_records(
    rows: Iterable[Dict[str, str]],
    centroids: Dict[str, Dict[str, float]],
    fallback_centroids: Dict[str, Dict[str, float]],
) -> List[Dict[str, str]]:
    transformed: List[Dict[str, str]] = []
    skipped = 0
    missing_centroids = 0
    for row in rows:
        raw_zip = (row.get("zipcode") or "").strip()
        if not is_valid_zip(raw_zip):
            skipped += 1
            LOGGER.debug("Skipping non-standard ZIP entry: %s", raw_zip)
            continue
        city = (row.get("city") or "Unknown").strip() or "Unknown"
        county = (row.get("county") or "Unknown").strip() or "Unknown"
        city = city.title()
        county = county.title()
        zip_key = raw_zip.zfill(5)
        centroid = (
            centroids.get(zip_key)
            or fallback_centroids.get(zip_key)
            or MANUAL_CENTROID_OVERRIDES.get(zip_key)
        )
        if centroid is None:
            missing_centroids += 1
        latitude = f"{centroid['latitude']:.6f}" if centroid else ""
        longitude = f"{centroid['longitude']:.6f}" if centroid else ""
        transformed.append(
            {
                "zip": zip_key,
                "city": city,
                "state": (row.get("state_abbr") or "").strip(),
                "county": county,
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    LOGGER.info(
        "Transformed %d rows (skipped %d, missing centroids for %d)",
        len(transformed),
        skipped,
        missing_centroids,
    )
    return transformed


def validate_records(records: List[Dict[str, str]], min_expected: int) -> None:
    if len(records) < min_expected:
        raise ValueError(f"Expected at least {min_expected} ZIP codes, got {len(records)}")

    missing = [idx for idx, rec in enumerate(records) if any(not rec[field] for field in REQUIRED_FIELDS)]
    if missing:
        raise ValueError(f"Records missing required fields at indexes: {missing[:5]}")

    non_ca = [rec for rec in records if rec.get("state") != "CA"]
    if non_ca:
        raise ValueError(f"Encountered {len(non_ca)} non-CA records after filtering")


def deduplicate(records: List[Dict[str, str]]) -> List[Dict[str, str]]:
    deduped: Dict[str, Dict[str, str]] = {}
    for rec in records:
        deduped.setdefault(rec["zip"], rec)
    LOGGER.info("Deduplicated to %d unique ZIP codes", len(deduped))
    return list(deduped.values())


def write_csv(records: List[Dict[str, str]], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Writing %d records to %s", len(records), output_csv)
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_HEADERS)
        writer.writeheader()
        for record in sorted(records, key=lambda item: item["zip"]):
            writer.writerow({header: record.get(header, "") for header in OUTPUT_HEADERS})


def write_metadata(
    metadata_json: Path,
    *,
    source_url: str,
    centroid_source_url: str,
    fallback_centroid_source_url: str,
    record_count: int,
) -> None:
    metadata_json.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "source_url": source_url,
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
        "record_count": record_count,
        "fields": OUTPUT_HEADERS,
        "centroid_source_url": centroid_source_url,
        "fallback_centroid_source_url": fallback_centroid_source_url,
        "notes": (
            "Dataset sourced from scpike/us-state-county-zip, OpenDataDE centroid GeoJSON, "
            "USCities JSON fallback repositories, plus manual overrides for select non-ZCTA ZIPs."
        ),
    }
    LOGGER.info("Writing metadata to %s", metadata_json)
    metadata_json.write_text(json.dumps(metadata, indent=2))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    configure_logging(args.verbose)

    raw_csv = fetch_dataset(args.source_url, args.timeout)
    records = list(parse_records(raw_csv))
    california_rows = filter_california(records)
    centroids = fetch_centroids(args.centroid_url, args.timeout)
    fallback_centroids = fetch_fallback_centroids(args.fallback_centroid_url, args.timeout)
    transformed = transform_records(california_rows, centroids, fallback_centroids)
    validate_records(transformed, args.min_expected)
    deduped = deduplicate(transformed)

    write_csv(deduped, args.output_csv)
    write_metadata(
        args.metadata_json,
        source_url=args.source_url,
        centroid_source_url=args.centroid_url,
        fallback_centroid_source_url=args.fallback_centroid_url,
        record_count=len(deduped),
    )

    LOGGER.info("ZIP ingestion completed successfully with %d entries", len(deduped))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
