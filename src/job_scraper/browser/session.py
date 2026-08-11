from __future__ import annotations

import json
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from job_scraper.application.application_runtime import ApplicationRuntime
from job_scraper.application.session_state import (
    ApplicationSessionState,
    save_session_state,
)
from job_scraper.domain.models import ApplicationJob


class BrowserSessionError(RuntimeError):
    """Raised when the long-running browser session cannot be managed safely."""


@dataclass(frozen=True, slots=True)
class BrowserSessionStart:
    pid: int
    port: int
    reused: bool


def start_browser_session(
    runtime: ApplicationRuntime,
    *,
    canonical_job_id: str,
    requested_url: str,
) -> BrowserSessionStart:
    from job_scraper.browser.chrome_cdp import _assert_public_https_url

    _assert_public_https_url(requested_url)
    if cdp_is_ready(runtime):
        return BrowserSessionStart(pid=0, port=runtime.browser_debug_port, reused=True)
    executable = _browser_executable(runtime.browser_channel)
    arguments = [
        executable,
        f"--user-data-dir={runtime.browser_profile_dir}",
        f"--remote-debugging-port={runtime.browser_debug_port}",
        "--new-window",
        requested_url,
    ]
    process = subprocess.Popen(arguments, close_fds=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if cdp_is_ready(runtime):
            save_session_state(
                runtime,
                ApplicationSessionState(
                    status="running",
                    canonical_job_id=canonical_job_id,
                    platform="",
                    requested_url=requested_url,
                    current_url=requested_url,
                    step="landing",
                ),
            )
            return BrowserSessionStart(
                pid=process.pid,
                port=runtime.browser_debug_port,
                reused=False,
            )
        time.sleep(0.25)
    process.kill()
    raise BrowserSessionError("Dedicated browser did not expose its local debug port")


def run_application_session(
    runtime: ApplicationRuntime,
    *,
    canonical_job_id: str,
    requested_url: str,
) -> None:
    """Keep one visible browser alive while a human handles gated steps."""

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    from job_scraper.browser.chrome_cdp import _assert_public_https_url, _find_apply_ctas

    _assert_public_https_url(requested_url)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(runtime.browser_profile_dir),
            channel=runtime.browser_channel,
            headless=False,
        )
        page = context.new_page()
        current_url = requested_url
        save_session_state(
            runtime,
            ApplicationSessionState(
                status="running",
                canonical_job_id=canonical_job_id,
                platform="",
                requested_url=requested_url,
                current_url=requested_url,
                step="landing",
            ),
        )
        try:
            page.goto(requested_url, wait_until="domcontentloaded", timeout=30_000)
            prefilled = False
            filled_fields: tuple[str, ...] = ()
            cv_attached = False
            while True:
                current_url = page.url
                _assert_public_https_url(current_url)
                if page.locator("form").count():
                    if not prefilled:
                        filled_fields, cv_attached = _prefill_zoho_form(page, runtime)
                        prefilled = True
                    save_session_state(
                        runtime,
                        _session_state(
                            canonical_job_id,
                            requested_url,
                            current_url,
                            "form",
                            "review and complete CAPTCHA; filled "
                            f"{len(filled_fields)} fields; CV attached={'yes' if cv_attached else 'no'}",
                        ),
                    )
                else:
                    ctas = _find_apply_ctas(page.locator("a,button").all_inner_texts())
                    if not ctas:
                        save_session_state(
                            runtime,
                            _session_state(
                                canonical_job_id,
                                requested_url,
                                current_url,
                                "waiting_for_human",
                                "complete login, consent, or challenge in the visible browser",
                            ),
                        )
                        time.sleep(2)
                        continue
                    locator = page.locator("a,button").filter(has_text=ctas[0]).first
                    try:
                        if locator.get_attribute("target") == "_blank":
                            with context.expect_page(timeout=10_000) as popup_info:
                                locator.click(timeout=10_000)
                            page = popup_info.value
                        else:
                            locator.click(timeout=10_000)
                    except PlaywrightError:
                        save_session_state(
                            runtime,
                            _session_state(
                                canonical_job_id,
                                requested_url,
                                current_url,
                                "waiting_for_human",
                                "clear the visible overlay or complete the human challenge",
                            ),
                        )
                        time.sleep(2)
                        continue
                    with suppress(PlaywrightError):
                        page.wait_for_load_state("domcontentloaded", timeout=10_000)
                time.sleep(2)
        except KeyboardInterrupt:
            save_session_state(
                runtime,
                _session_state(
                    canonical_job_id,
                    requested_url,
                    current_url,
                    "stopped",
                ),
            )
        except PlaywrightError as exc:
            save_session_state(
                runtime,
                _session_state(
                    canonical_job_id,
                    requested_url,
                    current_url,
                    "error",
                    str(exc.__class__.__name__),
                ),
            )
            raise BrowserSessionError(f"Browser session failed: {exc.__class__.__name__}") from exc
        finally:
            page.close()
            context.close()


def run_application_batch(runtime: ApplicationRuntime, jobs: list[ApplicationJob]) -> None:
    """Open up to twenty accepted jobs and prepare their forms without submitting."""

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    from job_scraper.browser.chrome_cdp import _assert_public_https_url

    if not 1 <= len(jobs) <= 20:
        raise ValueError("application batch must contain between 1 and 20 jobs")
    for job in jobs:
        _assert_public_https_url(job.application_url)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(runtime.browser_profile_dir),
            channel=runtime.browser_channel,
            headless=False,
        )
        pages: list[tuple[ApplicationJob, Any]] = []
        prepared = 0
        try:
            for job in jobs:
                page = context.new_page()
                pages.append((job, page))
                try:
                    page.goto(job.application_url, wait_until="domcontentloaded", timeout=30_000)
                except PlaywrightError:
                    continue
                save_session_state(
                    runtime,
                    _session_state(
                        job.canonical_job_id,
                        job.application_url,
                        page.url,
                        "batch_opened",
                        f"opened tab {len(pages)}/{len(jobs)}; no submission performed",
                    ),
                )

            for index, (job, page) in enumerate(pages, start=1):
                if page.is_closed():
                    continue
                page, step, action = _prepare_batch_page(page, context, runtime)
                pages[index - 1] = (job, page)
                prepared += 1
                save_session_state(
                    runtime,
                    _session_state(
                        job.canonical_job_id,
                        job.application_url,
                        page.url,
                        step,
                        f"batch {prepared}/{len(jobs)}; {action}; no submission performed",
                    ),
                )

            save_session_state(
                runtime,
                _session_state(
                    jobs[-1].canonical_job_id,
                    jobs[-1].application_url,
                    pages[-1][1].url,
                    "batch_ready",
                    f"{prepared}/{len(jobs)} tabs prepared; review and submit manually",
                ),
            )
            while True:
                time.sleep(2)
        except KeyboardInterrupt:
            save_session_state(
                runtime,
                _session_state(
                    jobs[-1].canonical_job_id,
                    jobs[-1].application_url,
                    pages[-1][1].url if pages else jobs[-1].application_url,
                    "stopped",
                    f"batch stopped after preparing {prepared}/{len(jobs)} tabs",
                ),
            )
        finally:
            context.close()


def _prepare_batch_page(
    page: Any, context: Any, runtime: ApplicationRuntime
) -> tuple[Any, str, str]:
    from playwright.sync_api import Error as PlaywrightError

    for _ in range(4):
        if page.locator("form").count():
            filled_fields, cv_attached = _prefill_zoho_form(page, runtime)
            return (
                page,
                "form",
                f"filled {len(filled_fields)} fields; CV attached={'yes' if cv_attached else 'no'}; CAPTCHA left blank",
            )
        ctas = _find_apply_ctas_for_page(page)
        if not ctas:
            return page, "waiting_for_human", "login, consent, or challenge requires review"
        locator = page.locator("a,button").filter(has_text=ctas[0]).first
        try:
            if locator.get_attribute("target") == "_blank":
                with context.expect_page(timeout=10_000) as popup_info:
                    locator.click(timeout=10_000)
                page = popup_info.value
            else:
                locator.click(timeout=10_000)
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except PlaywrightError:
            return page, "waiting_for_human", "clear the visible overlay or challenge"
    return page, "waiting_for_human", "application flow needs review"


def _find_apply_ctas_for_page(page: Any) -> tuple[str, ...]:
    return _find_preparation_ctas(page.locator("a,button").all_inner_texts())


def _find_preparation_ctas(texts: list[str]) -> tuple[str, ...]:
    allowed_markers = ("apply", "bewerben", "i'm interested", "interested")
    blocked_markers = ("submit", "send application", "consent", "agree", "continue")
    candidates: list[str] = []
    for text in texts:
        normalized = " ".join(text.casefold().split())
        if (
            normalized
            and any(marker in normalized for marker in allowed_markers)
            and not any(marker in normalized for marker in blocked_markers)
            and text not in candidates
        ):
            candidates.append(text)
    return tuple(candidates)


def _prefill_zoho_form(page: Any, runtime: ApplicationRuntime) -> tuple[tuple[str, ...], bool]:
    """Fill the first supported ATS form from confirmed private runtime facts."""

    if "zohorecruit" not in page.url.casefold():
        return (), False
    payload = json.loads(runtime.facts_file.read_text(encoding="utf-8"))
    identity = _private_identity(payload)
    full_name = identity.get("full_name", "").split(maxsplit=1)
    if len(full_name) < 2:
        return (), False
    values = {
        1: full_name[0],
        2: full_name[1],
        3: str(identity.get("email", "")).strip(),
        5: _phone_digits(str(identity.get("phone", ""))),
        6: str(identity.get("location", "")).strip(),
    }
    labels = {1: "first_name", 2: "last_name", 3: "email", 5: "mobile", 6: "city"}
    inputs = page.locator("input")
    filled: list[str] = []
    for index, value in values.items():
        if value and index < inputs.count() and not inputs.nth(index).input_value():
            inputs.nth(index).fill(value)
            filled.append(labels[index])
    resume = _approved_resume(runtime, payload)
    files = page.locator("input[type=file]")
    cv_attached = False
    if resume is not None and resume.is_file() and files.count():
        files.nth(min(1, files.count() - 1)).set_input_files(str(resume))
        cv_attached = True
    return tuple(filled), cv_attached


def _private_identity(payload: object) -> dict[str, str]:
    """Extract confirmed identity facts from the private facts-array schema."""

    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for item in payload.get("facts", []):
        if not isinstance(item, dict):
            continue
        fact_id = item.get("id")
        value = item.get("value")
        if isinstance(fact_id, str) and fact_id.startswith("identity."):
            result[fact_id.removeprefix("identity.")] = str(value or "").strip()
    return result


def _approved_resume(runtime: ApplicationRuntime, facts: object) -> Path | None:
    if not isinstance(facts, dict):
        return None
    for value in _walk_values(facts):
        if not isinstance(value, dict):
            continue
        path = value.get("path")
        status = str(value.get("status", "")).casefold()
        role = " ".join(
            str(value.get(key, "")) for key in ("role", "kind", "name", "purpose", "id")
        ).casefold()
        if isinstance(path, str) and "resume" in role and status == "approved":
            candidate = Path(path)
            resolved = (
                candidate if candidate.is_absolute() else runtime.root / candidate
            ).resolve()
            if resolved.is_relative_to(
                runtime.documents_dir.resolve()
            ) and resolved.suffix.casefold() in {".pdf", ".doc", ".docx"}:
                return resolved
    return None


def _walk_values(value: object) -> list[object]:
    if isinstance(value, dict):
        return [value, *[child for item in value.values() for child in _walk_values(item)]]
    if isinstance(value, list):
        return [child for item in value for child in _walk_values(item)]
    return []


def _phone_digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())[-12:]


def _session_state(
    canonical_job_id: str,
    requested_url: str,
    current_url: str,
    step: str,
    human_action: str = "",
) -> ApplicationSessionState:
    return ApplicationSessionState(
        status="running",
        canonical_job_id=canonical_job_id,
        platform="",
        requested_url=requested_url,
        current_url=current_url,
        step=step,
        human_action=human_action,
    )


def cdp_is_ready(runtime: ApplicationRuntime) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{runtime.browser_debug_port}/json/version", timeout=1):
            return True
    except (OSError, URLError):
        return False


def _browser_executable(channel: str) -> str:
    candidates = {
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "msedge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    }
    if channel == "chromium":
        raise BrowserSessionError("Chromium session launch requires an explicit executable")
    executable = candidates[channel]
    if not Path(executable).is_file():
        raise BrowserSessionError(f"Browser executable not found: {executable}")
    return executable
