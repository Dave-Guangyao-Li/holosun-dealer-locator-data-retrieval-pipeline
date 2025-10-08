"""Proof-of-concept fetcher for a single California ZIP code.

This utility loads cached centroid coordinates from ``data/processed/ca_zip_codes.csv``
(or a caller-provided CSV), submits a Holosun dealer lookup for the requested ZIP,
detects basic anti-automation responses, and writes both the raw API payload and a
lightweight normalized summary to ``data/raw/single_zip_runs/<timestamp>_<zip>/``.

Example:
    poetry run python scripts/fetch_single_zip.py 90001
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import requests

LOGGER = logging.getLogger("holosun.single_zip")
SEARCH_ENDPOINT = "https://holosun.com/index/dealer/search.html"
ANTI_AUTOMATION_KEYWORDS = (
    "captcha",
    "access denied",
    "forbidden",
    "bot detection",
    "unusual traffic",
    "warning",
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/119.0.0.0 Safari/537.36"
)


class AntiAutomationError(RuntimeError):
    """Raised when the Holosun endpoint returns a response that looks like a block."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_code", help="5-digit California ZIP code to query.")
    parser.add_argument(
        "--zip-csv",
        type=Path,
        default=Path("data/processed/ca_zip_codes.csv"),
        help="CSV file containing ZIP latitude/longitude centroids.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw/single_zip_runs"),
        help="Directory to store fetch artifacts (request/response/summary).",
    )
    parser.add_argument(
        "--distance",
        type=int,
        default=100,
        help="Distance radius parameter submitted with the request.",
    )
    parser.add_argument(
        "--category",
        default="both",
        help="Holosun category parameter (defaults to 'both').",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="User-Agent header to use for the request.",
    )
    parser.add_argument(
        "--skip-write",
        action="store_true",
        help="Do not write artifacts to disk (useful for smoke tests).",
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


def load_zip_centroids(csv_path: Path) -> Dict[str, Dict[str, Any]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"ZIP centroid CSV not found: {csv_path}")

    LOGGER.debug("Loading centroid CSV: %s", csv_path)
    mapping: Dict[str, Dict[str, Any]] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            zip_code = (row.get("zip") or "").strip().zfill(5)
            if not zip_code or len(zip_code) != 5 or not zip_code.isdigit():
                continue
            latitude = row.get("latitude")
            longitude = row.get("longitude")
            try:
                lat_val = float(latitude) if latitude else None
                lon_val = float(longitude) if longitude else None
            except (TypeError, ValueError):
                lat_val = None
                lon_val = None
            mapping[zip_code] = {
                "zip": zip_code,
                "city": (row.get("city") or "").strip() or None,
                "county": (row.get("county") or "").strip() or None,
                "state": (row.get("state") or "").strip() or None,
                "latitude": lat_val,
                "longitude": lon_val,
            }
    if not mapping:
        raise ValueError(f"No ZIP entries loaded from {csv_path}")
    LOGGER.debug("Loaded %d ZIP centroid rows", len(mapping))
    return mapping


def prepare_payload(zip_code: str, centroid: Dict[str, Any], distance: int, category: str) -> Dict[str, str]:
    lat = centroid.get("latitude")
    lon = centroid.get("longitude")
    if lat is None or lon is None:
        raise ValueError(f"ZIP {zip_code} is missing centroid coordinates")

    payload = {
        "keywords": zip_code,
        "distance": str(distance),
        "lat": f"{lat:.6f}",
        "lng": f"{lon:.6f}",
        "cate": category,
    }
    return payload


def perform_request(
    payload: Dict[str, str],
    *,
    timeout: int,
    user_agent: str,
) -> Tuple[requests.Response, str]:
    headers = {
        "User-Agent": user_agent,
        "Referer": "https://holosun.com/where-to-buy.html?c=both",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    LOGGER.debug("Submitting POST to %s with payload %s", SEARCH_ENDPOINT, payload)
    response = requests.post(SEARCH_ENDPOINT, data=payload, headers=headers, timeout=timeout)
    body_text = response.text
    return response, body_text


def detect_anti_automation(response: requests.Response, body_text: str) -> List[str]:
    issues: List[str] = []
    status = response.status_code
    if status in {403, 429, 503} or status >= 500:
        issues.append(f"Unexpected status code {status}")

    content_type = (response.headers.get("content-type") or "").lower()
    if "json" not in content_type:
        lowered = body_text.lower()
        if any(keyword in lowered for keyword in ANTI_AUTOMATION_KEYWORDS):
            issues.append("Response body appears to be an anti-automation warning page")
        else:
            issues.append(f"Unexpected content-type '{content_type}' (expected JSON)")

    try:
        payload = response.json()
    except ValueError:
        issues.append("Failed to parse response body as JSON")
        return issues

    code = payload.get("code")
    if code != 1:
        issues.append(f"Holosun API returned non-success code: {code!r}")
    return issues


def normalize_dealer(raw: Dict[str, Any], source_zip: str) -> Dict[str, Any]:
    phone_raw = (raw.get("phone") or raw.get("tel") or "").strip()
    phone_clean = phone_raw
    if phone_clean.lower().startswith("phone:"):
        phone_clean = phone_clean.split(":", 1)[-1].strip()
    phone_clean = phone_clean.strip()

    address_raw = (raw.get("contact_addr") or raw.get("contact") or "").replace("\r", "")
    address_lines = [line.strip() for line in address_raw.splitlines() if line and line.strip()]
    address_text = ", ".join(address_lines)

    def _to_float(value: Any) -> float | None:
        if value in (None, "", "0", "0.0"):
            try:
                return float(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    lat = _to_float(raw.get("lat"))
    lon = _to_float(raw.get("lng"))

    website = (raw.get("website") or "").strip() or None
    dealer_name = (raw.get("company_name") or "").strip() or None

    normalized = {
        "dealer_name": dealer_name,
        "phone": phone_clean or None,
        "website": website,
        "address_lines": address_lines,
        "address_text": address_text or None,
        "latitude": lat,
        "longitude": lon,
        "holosun_id": raw.get("id"),
        "record_zip": (raw.get("zip") or "").strip() or None,
        "source_zip": source_zip,
        "category": (raw.get("category") or "").strip() or None,
        "emails": [email.strip() for email in (raw.get("email") or "").split(",") if email.strip()],
    }
    return normalized


def write_artifacts(
    output_dir: Path,
    zip_code: str,
    payload: Dict[str, str],
    response: requests.Response,
    response_body: Dict[str, Any],
    normalized: List[Dict[str, Any]],
    centroid: Dict[str, Any],
) -> Path:
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_dir / f"{timestamp}_{zip_code}"
    run_dir.mkdir(parents=True, exist_ok=True)

    request_artifact = {
        "url": SEARCH_ENDPOINT,
        "zip_code": zip_code,
        "payload": payload,
        "headers": {
            "User-Agent": response.request.headers.get("User-Agent"),
            "Referer": response.request.headers.get("Referer"),
            "X-Requested-With": response.request.headers.get("X-Requested-With"),
        },
        "centroid": centroid,
        "requested_at": timestamp,
    }
    (run_dir / "request.json").write_text(json.dumps(request_artifact, indent=2))
    (run_dir / "response.json").write_text(json.dumps(response_body, indent=2))

    summary = {
        "zip_code": zip_code,
        "requested_at": timestamp,
        "dealer_count": len(normalized),
        "normalized_dealers": normalized,
        "response_headers": dict(response.headers),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    LOGGER.info("Artifacts written to %s", run_dir)
    return run_dir


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    configure_logging(args.verbose)

    zip_code = args.zip_code.strip()
    if not zip_code.isdigit() or len(zip_code) != 5:
        LOGGER.error("ZIP code must be a 5-digit numeric string: %s", zip_code)
        return 2
    zip_code = zip_code.zfill(5)

    try:
        centroids = load_zip_centroids(args.zip_csv)
    except Exception as exc:  # pragma: no cover - surfaced to CLI
        LOGGER.error("Failed to load ZIP centroids: %s", exc)
        return 2

    centroid = centroids.get(zip_code)
    if not centroid:
        LOGGER.error("ZIP %s not found in centroid CSV %s", zip_code, args.zip_csv)
        return 2

    try:
        payload = prepare_payload(zip_code, centroid, args.distance, args.category)
    except Exception as exc:
        LOGGER.error("Failed to prepare request payload: %s", exc)
        return 2

    try:
        response, body_text = perform_request(payload, timeout=args.timeout, user_agent=args.user_agent)
        issues = detect_anti_automation(response, body_text)
        if issues:
            raise AntiAutomationError("; ".join(issues))
        response_body = response.json()
    except AntiAutomationError as exc:
        LOGGER.error("Potential anti-automation response detected: %s", exc)
        if not args.skip_write:
            timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            failure_dir = args.output_dir / f"{timestamp}_{zip_code}_blocked"
            failure_dir.mkdir(parents=True, exist_ok=True)
            failure_payload = {
                "payload": payload,
                "status_code": response.status_code if 'response' in locals() else None,
                "headers": dict(response.headers) if 'response' in locals() else None,
                "body_snippet": body_text[:1000] if 'body_text' in locals() else None,
                "issues": str(exc),
            }
            (failure_dir / "blocked.json").write_text(json.dumps(failure_payload, indent=2))
            LOGGER.info("Saved blocked response artifact to %s", failure_dir)
        return 3
    except Exception as exc:
        LOGGER.error("Fetch failed: %s", exc)
        return 1

    dealers: Iterable[Dict[str, Any]] = response_body.get("data", {}).get("list", []) or []
    normalized = [normalize_dealer(raw, zip_code) for raw in dealers]

    LOGGER.info("Fetched %d dealer records for ZIP %s", len(normalized), zip_code)
    for dealer in normalized:
        LOGGER.info(
            "Dealer: %s | Phone: %s | ZIP: %s",
            dealer.get("dealer_name") or "<unknown>",
            dealer.get("phone") or "<none>",
            dealer.get("record_zip") or "<unknown>",
        )

    if not args.skip_write:
        try:
            write_artifacts(
                args.output_dir,
                zip_code,
                payload,
                response,
                response_body,
                normalized,
                centroid,
            )
        except Exception as exc:
            LOGGER.error("Failed to write artifacts: %s", exc)
            return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
