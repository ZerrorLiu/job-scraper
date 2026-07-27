from __future__ import annotations

import ipaddress
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
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
    apply_ctas: tuple[str, ...]
    screenshot_path: Path
    redirected: bool
    followed_url: str | None = None
    followed_title: str | None = None
    followed_form_count: int | None = None
    followed_screenshot_path: Path | None = None


def inspect_application_page(
    runtime: ApplicationRuntime,
    application_url: str,
    *,
    follow_apply: bool = False,
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
                apply_ctas=_find_apply_ctas(page.locator("a,button").all_inner_texts()),
                screenshot_path=screenshot_path,
                redirected=_different_destination(application_url, final_url),
            )
            if follow_apply:
                result = _follow_apply_cta(context, page, result, runtime)
            page.close()
            context.close()
            return result
    except BrowserConnectionError:
        raise
    except PlaywrightError as exc:
        raise BrowserConnectionError(
            f"Browser inspection failed: {exc.__class__.__name__}"
        ) from exc


def _follow_apply_cta(
    context: Any,
    page: Any,
    result: PageInspection,
    runtime: ApplicationRuntime,
) -> PageInspection:
    from playwright.sync_api import Error as PlaywrightError

    if not result.apply_ctas:
        raise BrowserConnectionError("No application CTA was found on the page")
    locator = page.locator("a,button").filter(has_text=result.apply_ctas[0]).first
    try:
        locator.click(timeout=10_000)
    except PlaywrightError as exc:
        raise BrowserConnectionError(
            "Application CTA could not be clicked; a consent or authentication "
            "overlay may be blocking it"
        ) from exc
    pages = context.pages
    followed_page = pages[-1]
    with suppress(PlaywrightError):
        followed_page.wait_for_load_state("domcontentloaded", timeout=10_000)
    followed_url = followed_page.url
    _assert_public_https_url(followed_url)
    followed_screenshot_path = runtime.evidence_dir / f"application-followed-{uuid4().hex}.png"
    followed_page.screenshot(path=str(followed_screenshot_path), full_page=True)
    return PageInspection(
        requested_url=result.requested_url,
        final_url=result.final_url,
        title=result.title,
        form_count=result.form_count,
        apply_ctas=result.apply_ctas,
        screenshot_path=result.screenshot_path,
        redirected=result.redirected,
        followed_url=followed_url,
        followed_title=followed_page.title(),
        followed_form_count=followed_page.locator("form").count(),
        followed_screenshot_path=followed_screenshot_path,
    )


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


def _find_apply_ctas(labels: list[str]) -> tuple[str, ...]:
    matches: list[str] = []
    for raw_label in labels:
        label = " ".join(raw_label.split())
        lowered = label.casefold()
        if (
            label
            and any(term in lowered for term in ("apply", "bewerben", "submit"))
            and label not in matches
        ):
            matches.append(label[:120])
    return tuple(matches[:5])
