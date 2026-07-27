from __future__ import annotations

import argparse
import sys
from pathlib import Path

from job_scraper.adapters.storage.sqlite_v2 import WorkspaceDatabase
from job_scraper.application.application_doctor import inspect_application_runtime
from job_scraper.application.application_runtime import (
    ApplicationRuntime,
    RuntimeConfigurationError,
)
from job_scraper.application.browser_inspection import (
    ApplicationInspectionError,
    inspect_accepted_job,
)


def add_apply_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    apply = subparsers.add_parser(
        "apply", help="Run the private real-browser application workflow."
    )
    commands = apply.add_subparsers(dest="apply_command", required=True)
    doctor = commands.add_parser(
        "doctor", help="Check the private runtime and dedicated browser prerequisites."
    )
    doctor.add_argument("--workspace", type=Path, help="Private application runtime directory.")
    inspect = commands.add_parser(
        "inspect",
        help="Open one accepted job in a dedicated real browser; inspect only, never submit.",
        description=(
            "Launch the runtime's dedicated browser, visit the real application URL, "
            "count forms, and save a private screenshot. This command never fills or submits."
        ),
    )
    inspect.add_argument("--job-id", required=True, help="Canonical accepted job ID.")
    inspect.add_argument("--workspace", type=Path, help="Private application runtime directory.")


def run_apply(args: argparse.Namespace) -> int:
    if args.apply_command not in {"doctor", "inspect"}:
        raise AssertionError(f"Unhandled apply command: {args.apply_command}")
    try:
        runtime = ApplicationRuntime.from_environment(root=args.workspace)
    except RuntimeConfigurationError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    if args.apply_command == "doctor":
        checks = inspect_application_runtime(runtime)
        for check in checks:
            print(f"{'PASS' if check.ok else 'FAIL'} {check.name}: {check.detail}")
        return 0 if all(check.ok for check in checks) else 2
    checks = inspect_application_runtime(runtime)
    if not all(check.ok for check in checks):
        print("ERROR apply inspect requires a passing apply doctor", file=sys.stderr)
        for check in checks:
            print(f"{'PASS' if check.ok else 'FAIL'} {check.name}: {check.detail}")
        return 2
    try:
        report = inspect_accepted_job(
            WorkspaceDatabase(runtime.workspace_database),
            runtime,
            args.job_id,
        )
    except (ApplicationInspectionError, OSError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    print(f"INSPECTED job={report.canonical_job_id}")
    print(f"TITLE {report.title}")
    print(f"COMPANY {report.company_name}")
    print(f"REDIRECTED {'yes' if report.inspection.redirected else 'no'}")
    print(f"REQUESTED_URL {report.inspection.requested_url}")
    print(f"URL {report.inspection.final_url}")
    print(f"PAGE_TITLE {report.inspection.title}")
    print(f"FORMS {report.inspection.form_count}")
    print(f"APPLY_CTAS {report.inspection.apply_ctas}")
    print(f"SCREENSHOT {report.inspection.screenshot_path}")
    return 0
