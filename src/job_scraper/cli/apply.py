from __future__ import annotations

import argparse
import sys
from pathlib import Path

from job_scraper.application.application_doctor import inspect_application_runtime
from job_scraper.application.application_runtime import (
    ApplicationRuntime,
    RuntimeConfigurationError,
)


def add_apply_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    apply = subparsers.add_parser(
        "apply", help="Run the private real-browser application workflow."
    )
    commands = apply.add_subparsers(dest="apply_command", required=True)
    doctor = commands.add_parser("doctor", help="Check private runtime and local Chrome CDP.")
    doctor.add_argument("--workspace", type=Path, help="Private application runtime directory.")


def run_apply(args: argparse.Namespace) -> int:
    if args.apply_command != "doctor":
        raise AssertionError(f"Unhandled apply command: {args.apply_command}")
    try:
        runtime = ApplicationRuntime.from_environment(root=args.workspace)
    except RuntimeConfigurationError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    checks = inspect_application_runtime(runtime)
    for check in checks:
        print(f"{'PASS' if check.ok else 'FAIL'} {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 2
