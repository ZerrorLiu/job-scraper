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
            while True:
                current_url = page.url
                _assert_public_https_url(current_url)
                if page.locator("form").count():
                    if not prefilled:
                        _prefill_zoho_form(page, runtime)
                        prefilled = True
                    save_session_state(
                        runtime,
                        _session_state(
                            canonical_job_id,
                            requested_url,
                            current_url,
                            "form",
                            "review and complete any required human challenge",
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


def _prefill_zoho_form(page: Any, runtime: ApplicationRuntime) -> None:
    """Fill the first supported ATS form from confirmed private runtime facts."""

    facts = json.loads(runtime.facts_file.read_text(encoding="utf-8"))
    identity = facts.get("identity", {})
    if not isinstance(identity, dict):
        return
    full_name = str(identity.get("full_name", "")).strip().split(maxsplit=1)
    if len(full_name) < 2:
        return
    values = {
        1: full_name[0],
        2: full_name[1],
        3: str(identity.get("email", "")).strip(),
        5: _phone_digits(str(identity.get("phone", ""))),
        6: str(identity.get("location", "")).strip(),
        7: "Germany",
    }
    inputs = page.locator("input")
    for index, value in values.items():
        if value and index < inputs.count() and not inputs.nth(index).input_value():
            inputs.nth(index).fill(value)
    resume = _approved_resume(runtime, facts)
    files = page.locator("input[type=file]")
    if resume is not None and resume.is_file() and files.count():
        files.nth(min(1, files.count() - 1)).set_input_files(str(resume))


def _approved_resume(runtime: ApplicationRuntime, facts: object) -> Path | None:
    if not isinstance(facts, dict):
        return None
    for value in _walk_values(facts):
        if not isinstance(value, dict):
            continue
        path = value.get("path")
        status = str(value.get("status", "")).casefold()
        role = " ".join(str(value.get(key, "")) for key in ("role", "kind", "name")).casefold()
        if isinstance(path, str) and "resume" in role and status == "approved":
            candidate = Path(path)
            return candidate if candidate.is_absolute() else runtime.root / candidate
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
