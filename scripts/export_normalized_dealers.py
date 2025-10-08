"""Export normalized dealer data to CSV with validation metrics."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from holosun_locator.exports import (
    compute_metrics,
    export_dealers_to_csv,
    load_normalized_dealers,
    validate_dealers,
)


LOGGER = logging.getLogger("holosun.export")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to the orchestrator's normalized_dealers.json file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination CSV file (defaults to <input>.csv).",
    )
    parser.add_argument(
        "--metrics-json",
        type=Path,
        help="Optional path for writing computed metrics as JSON.",
    )
    parser.add_argument(
        "--list-delimiter",
        default="|",
        help="Delimiter used when flattening list fields into CSV columns (default '|').",
    )
    parser.add_argument(
        "--fail-on-validation",
        action="store_true",
        help="Exit with an error if validation issues are detected.",
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


def resolve_output_path(input_path: Path, output_arg: Path | None) -> Path:
    if output_arg:
        return output_arg
    return input_path.with_suffix(".csv")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    configure_logging(args.verbose)

    input_path = args.input
    output_path = resolve_output_path(input_path, args.output)
    metrics_path = args.metrics_json

    try:
        dealers = load_normalized_dealers(input_path)
    except Exception as exc:
        LOGGER.error("Failed to load normalized dealers: %s", exc)
        return 2

    LOGGER.info("Loaded %d dealer records", len(dealers))

    validation_errors = validate_dealers(dealers)
    if validation_errors:
        for issue in validation_errors:
            LOGGER.warning("Validation issue: %s", issue)
        if args.fail_on_validation:
            LOGGER.error("Failing due to validation issues (%d)", len(validation_errors))
            return 3

    try:
        export_dealers_to_csv(dealers, output_path, list_delimiter=args.list_delimiter)
    except Exception as exc:
        LOGGER.error("Failed to export CSV: %s", exc)
        return 2

    LOGGER.info("Wrote CSV output to %s", output_path)

    metrics = compute_metrics(dealers)
    LOGGER.info("Metrics summary: %s", json.dumps(metrics, indent=2))

    if metrics_path:
        try:
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            LOGGER.info("Persisted metrics JSON to %s", metrics_path)
        except Exception as exc:
            LOGGER.error("Failed to write metrics JSON: %s", exc)
            return 2

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
