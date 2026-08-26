from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from job_scraper.config import AppConfig, load_config
from job_scraper.configuration import (
    get_config_root,
    load_profile_definition,
)
from job_scraper.configuration.brightdata import brightdata_direct_collection_enabled
from job_scraper.configuration.composition import apply_profile_to_runtime
from job_scraper.integrations.email_recommendations import load_email_ingest_config


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: str
    message: str


def run_doctor(
    profile_id: str,
    *,
    project_root: Path | None = None,
) -> tuple[CheckResult, ...]:
    root = project_root or Path(__file__).resolve().parents[3]
    config_root = (root / "config") if project_root is not None else get_config_root()
    results = [_python_check()]
    try:
        profile = load_profile_definition(
            profile_id,
            config_root=config_root,
        )
        config = load_config(profile.runtime_config)
        apply_profile_to_runtime(profile, config, config_root=config_root)
        results.append(
            CheckResult(
                "profile",
                "ok",
                f"{profile.profile_id}: {profile.runtime_config.name}",
            )
        )
        results.extend(_credential_checks(config))
        if "email_imap" in profile.channels:
            results.extend(_email_checks(config_root / "email.toml"))
    except (KeyError, OSError, TypeError, ValueError) as exc:
        results.append(CheckResult("profile", "error", str(exc)))

    results.extend(_privacy_checks(root))
    return tuple(results)


def has_errors(results: tuple[CheckResult, ...]) -> bool:
    return any(result.status == "error" for result in results)


def _python_check() -> CheckResult:
    version = sys.version_info
    if version < (3, 11):
        return CheckResult(
            "python",
            "error",
            f"Python {version.major}.{version.minor} is unsupported; use 3.11+",
        )
    return CheckResult(
        "python",
        "ok",
        f"Python {version.major}.{version.minor}.{version.micro}",
    )


def _credential_checks(config: AppConfig) -> list[CheckResult]:
    results: list[CheckResult] = []
    notion = config.notion
    indeed = config.sources.get("indeed_brightdata")
    if indeed is None or not indeed.enabled:
        results.append(
            CheckResult(
                "brightdata direct",
                "ok",
                "disabled by the effective profile source list",
            )
        )
    elif not brightdata_direct_collection_enabled():
        results.append(
            CheckResult(
                "brightdata direct",
                "ok",
                "suspended; BRIGHTDATA_DIRECT_COLLECTION_ENABLED is not true",
            )
        )
    else:
        results.append(
            _environment_check(
                "brightdata",
                ("BRIGHTDATA_API_KEY", "BRIGHTDATA_DATASET_ID"),
            )
        )
    if notion.enabled:
        results.append(_environment_check("notion", ("NOTION_INTEGRATION_TOKEN",)))
        if not (notion.parent_page_id or notion.database_id or notion.data_source_id):
            results.append(
                CheckResult(
                    "notion destination",
                    "error",
                    "set NOTION_PARENT_PAGE_ID, NOTION_DATABASE_ID, or a data_source_id",
                )
            )
        else:
            results.append(CheckResult("notion destination", "ok", "destination is configured"))
    return results


def _environment_check(name: str, variables: tuple[str, ...]) -> CheckResult:
    missing = [variable for variable in variables if not os.getenv(variable, "").strip()]
    if missing:
        return CheckResult(
            name,
            "warning",
            f"optional credentials missing: {', '.join(missing)}",
        )
    return CheckResult(name, "ok", "credentials are configured")


def _email_checks(path: Path) -> list[CheckResult]:
    if not path.is_file():
        return [CheckResult("email", "error", f"missing configuration: {path}")]
    try:
        config = load_email_ingest_config(path)
    except (OSError, TypeError, ValueError) as exc:
        return [CheckResult("email", "error", str(exc))]
    results: list[CheckResult] = []
    if not config.host:
        results.append(CheckResult("email", "error", "IMAP host is not configured"))
    else:
        results.append(CheckResult("email", "ok", f"IMAP host: {config.host}"))
    if config.username and config.password:
        results.append(CheckResult("email credentials", "ok", "credentials are configured"))
    else:
        results.append(
            _environment_check(
                "email credentials",
                (
                    config.username_env or "JOB_EMAIL_USERNAME",
                    config.password_env or "JOB_EMAIL_APP_PASSWORD",
                ),
            )
        )
    return results


def _privacy_checks(root: Path) -> list[CheckResult]:
    sensitive_paths = [
        root / "my_cookies.json",
        root / "session_credentials.json",
        root / "config" / "email.local.toml",
    ]
    present = [path.relative_to(root).as_posix() for path in sensitive_paths if path.exists()]
    if not present:
        return [CheckResult("privacy", "ok", "no known local secret files detected")]
    return [
        CheckResult(
            "privacy",
            "warning",
            "local-only files present (must remain ignored): " + ", ".join(present),
        )
    ]
