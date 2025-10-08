"""Utility to record Holosun dealer locator network traffic for later analysis.

The script is meant for reconnaissance: it launches a Chromium instance using Playwright,
attaches listeners for XHR/fetch traffic, and persists both raw payloads and a
lightweight YAML summary so we can reverse engineer the locator API before
building the full pipeline.

Example usage (run from repository root):

    poetry run python scripts/capture_locator_traffic.py \
        --zip 94105 \
        --output-dir data/raw/network \
        --run-mode interactive

The script does not attempt to bypass anti-automation controls. If a CAPTCHA or
block is detected, it writes an entry into the summary file and pauses when
`--run-mode interactive` is selected so a human can intervene.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from playwright.async_api import Browser, Page, Playwright, async_playwright
except ModuleNotFoundError as exc:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "Playwright is required. Install with `pip install playwright` and run ``python -m playwright install``"
    ) from exc

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    yaml = None  # type: ignore[assignment]

LOGGER = logging.getLogger("holosun.recon")
DEFAULT_URL = "https://holosun.com/where-to-buy.html?c=both"
SUPPORTED_RESOURCE_TYPES = {"xhr", "fetch"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", dest="zip_code", required=False, help="ZIP code to submit during recon run")
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="Locator page URL to load. Override if Holosun changes their endpoint.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw/network"),
        help="Directory where raw payloads and summary artifacts will be stored.",
    )
    parser.add_argument(
        "--run-mode",
        choices=["headless", "headed", "interactive"],
        default="headless",
        help=(
            "headless: automated submission without UI. "
            "headed: show browser window but no prompts. "
            "interactive: headed + pauses for manual intervention when needed."
        ),
    )
    parser.add_argument(
        "--input-selector",
        default="input[name=zip]",
        help="CSS selector for the ZIP input field. Override if the site structure changes.",
    )
    parser.add_argument(
        "--submit-selector",
        default="button[type=submit]",
        help="CSS selector for the submit button that triggers the dealer search.",
    )
    parser.add_argument(
        "--wait-selector",
        default=".dealer-card",
        help="CSS selector that indicates results were rendered. Used to time completion.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15000,
        help="Timeout (ms) for waiting on selectors and navigation steps.",
    )
    parser.add_argument(
        "--max-responses",
        type=int,
        default=25,
        help="Maximum number of network responses to persist before the run stops automatically.",
    )
    return parser


def configure_logging(run_mode: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    LOGGER.info("Starting locator recon (mode=%s)", run_mode)


async def launch_browser(playwright: Playwright, run_mode: str) -> Browser:
    headless = run_mode == "headless"
    LOGGER.info("Launching Chromium (headless=%s)", headless)
    return await playwright.chromium.launch(headless=headless)


async def prepare_page(browser: Browser) -> Page:
    context = await browser.new_context()
    page = await context.new_page()
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    return page


def ensure_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Artifacts will be saved to %s", output_dir)


async def inject_listeners(page: Page, collector: "NetworkCollector") -> None:
    page.on("request", collector.handle_request)
    page.on("response", collector.handle_response)


class NetworkCollector:
    def __init__(self, output_dir: Path, max_responses: int) -> None:
        self.output_dir = output_dir
        self.max_responses = max_responses
        self.responses: List[Dict[str, Any]] = []

    async def handle_request(self, request) -> None:  # type: ignore[override]
        if request.resource_type not in SUPPORTED_RESOURCE_TYPES:
            return
        LOGGER.info("Request: %s %s", request.method, request.url)

    async def handle_response(self, response) -> None:  # type: ignore[override]
        request = response.request
        if request.resource_type not in SUPPORTED_RESOURCE_TYPES:
            return

        payload: Dict[str, Any] = {
            "url": response.url,
            "status": response.status,
            "method": request.method,
            "headers": await response.all_headers(),
            "request_headers": request.headers,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        try:
            body_bytes = await response.body()
        except Exception as exc:  # pragma: no cover - network/Playwright exceptions
            LOGGER.warning("Failed to read response body for %s: %s", response.url, exc)
            body_bytes = b""
        payload_path = self.output_dir / f"response_{len(self.responses):03d}.json"
        payload_path.write_bytes(body_bytes)
        LOGGER.info("Saved body -> %s (%d bytes)", payload_path, len(body_bytes))

        payload["body_path"] = str(payload_path)
        self.responses.append(payload)

        if len(self.responses) >= self.max_responses:
            LOGGER.info(
                "Reached max captured responses (%d); further responses will be ignored.",
                self.max_responses,
            )
            await response.finished()
            raise asyncio.CancelledError  # signal upstream to stop listening

    def export_summary(self) -> Dict[str, Any]:
        return {
            "captured_responses": self.responses,
            "total_count": len(self.responses),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }


async def submit_zip(page: Page, args: argparse.Namespace) -> None:
    LOGGER.info("Navigating to locator page: %s", args.url)
    await page.goto(args.url, wait_until="domcontentloaded")

    LOGGER.info("Filling ZIP code %s", args.zip_code)
    await page.fill(args.input_selector, args.zip_code, timeout=args.timeout)
    await page.click(args.submit_selector, timeout=args.timeout)
    LOGGER.info("Submitted search; waiting for results selector %s", args.wait_selector)
    await page.wait_for_selector(args.wait_selector, timeout=args.timeout)


async def run_capture(args: argparse.Namespace) -> Dict[str, Any]:
    ensure_output_dir(args.output_dir)

    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, args.run_mode)
        page = await prepare_page(browser)
        collector = NetworkCollector(args.output_dir, max_responses=args.max_responses)
        await inject_listeners(page, collector)

        anti_automation_events: List[str] = []

        try:
            if args.zip_code:
                await submit_zip(page, args)
            else:
                LOGGER.warning(
                    "No ZIP provided; open browser window and manually submit a search to capture traffic."
                )
                if args.run_mode in {"headed", "interactive"}:
                    input("Press Enter after you have triggered a search in the browser...")
                else:
                    LOGGER.error("Cannot proceed without ZIP input in headless mode; aborting run.")
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("Capture failed: %s", exc)
            anti_automation_events.append(str(exc))
        finally:
            LOGGER.info("Stopping trace and closing browser")
            try:
                trace_path = args.output_dir / "trace.zip"
                await page.context.tracing.stop(path=str(trace_path))
                LOGGER.info("Playwright trace saved to %s", trace_path)
            except Exception as exc:  # pragma: no cover
                LOGGER.warning("Failed to save trace: %s", exc)
            await browser.close()

        summary = collector.export_summary()
        summary["anti_automation_events"] = anti_automation_events
        summary["zip_code"] = args.zip_code
        summary["run_mode"] = args.run_mode
        summary["url"] = args.url
        return summary


def persist_summary(summary: Dict[str, Any], output_dir: Path) -> Path:
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"capture_{timestamp}.json"
    json_path.write_text(json.dumps(summary, indent=2))
    LOGGER.info("Summary JSON written -> %s", json_path)

    if yaml:
        yaml_path = output_dir / f"capture_{timestamp}.yml"
        with yaml_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(summary, handle, sort_keys=False)
        LOGGER.info("Summary YAML written -> %s", yaml_path)
    else:
        LOGGER.warning("PyYAML not installed; skipping YAML summary export")
        yaml_path = json_path
    return json_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.run_mode == "headless" and not args.zip_code:
        parser.error("--zip is required when --run-mode=headless")

    configure_logging(args.run_mode)

    summary = asyncio.run(run_capture(args))
    persist_summary(summary, args.output_dir)

    if summary.get("anti_automation_events"):
        LOGGER.warning("Potential anti-automation issues detected: %s", summary["anti_automation_events"])
        if args.run_mode == "interactive":
            input("Review issues above. Press Enter to exit.")
    else:
        LOGGER.info("Capture completed without flagged anti-automation events.")

    LOGGER.info(
        "Processed %d responses; artifacts stored in %s",
        summary.get("total_count", 0),
        args.output_dir,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
