from __future__ import annotations

import ipaddress
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from job_scraper.application.application_runtime import ApplicationRuntime


class BrowserConnectionError(RuntimeError):
    """Raised when the dedicated browser cannot be used safely."""


@dataclass(frozen=True, slots=True)
class PageInspection:
    requested_url: str
    final_url: str
    title: str
    form_count: int
    screenshot_path: Path
    redirected: bool


def inspect_application_page(
    runtime: ApplicationRuntime,
    application_url: str,
) -> PageInspection:
    _assert_public_https_url(application_url)
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserConnectionError("Playwright is not installed") from exc

    runtime.evidence_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = runtime.evidence_dir / f"application-inspection-{uuid4().hex}.png"
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(runtime.browser_profile_dir),
                channel=runtime.browser_channel,
                headless=False,
            )
            page = context.new_page()
            page.goto(application_url, wait_until="domcontentloaded", timeout=30_000)
            with suppress(PlaywrightError):
                page.wait_for_load_state("networkidle", timeout=10_000)
            page.screenshot(path=str(screenshot_path), full_page=True)
            final_url = page.url
            _assert_public_https_url(final_url)
            result = PageInspection(
                requested_url=application_url,
                final_url=final_url,
                title=page.title(),
                form_count=page.locator("form").count(),
                screenshot_path=screenshot_path,
                redirected=_different_destination(application_url, final_url),
            )
            page.close()
            context.close()
            return result
    except BrowserConnectionError:
        raise
    except PlaywrightError as exc:
        raise BrowserConnectionError(
            f"Browser inspection failed: {exc.__class__.__name__}"
        ) from exc


def _assert_public_https_url(value: str) -> None:
    parsed = urlparse(value.strip())
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise BrowserConnectionError(
            "Application URL must be an absolute HTTPS URL without credentials"
        )
    if hostname == "localhost" or hostname.endswith(".local"):
        raise BrowserConnectionError("Application URL must not target a local hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global or address.is_multicast:
        raise BrowserConnectionError(
            "Application URL must not target a private or reserved address"
        )


def _different_destination(requested: str, final: str) -> bool:
    requested_url = urlparse(requested)
    final_url = urlparse(final)
    return (requested_url.scheme, requested_url.netloc, requested_url.path) != (
        final_url.scheme,
        final_url.netloc,
        final_url.path,
    )
