from __future__ import annotations

import argparse
import inspect
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from job_scraper.application.acquisition import RequestCoalescer, RequestGate
from job_scraper.cli.console import LiveRunTable
from job_scraper.config import load_config
from job_scraper.configuration import available_profiles, load_profile_definition
from job_scraper.jobs import ingest_email_recommendations, run_daily
from job_scraper.storage.db import Database

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all configured daily job tracks.", allow_abbrev=False
    )
    parser.add_argument(
        "--config-dir",
        default=str(PROJECT_ROOT / "config"),
        help="Private configuration root. Defaults to JOB_SCRAPER_CONFIG_DIR or ./config.",
    )
    config_group = parser.add_mutually_exclusive_group()
    config_group.add_argument(
        "--config",
        help="Run exactly one explicit track config path (not the default all-track set).",
    )
    config_group.add_argument(
        "--configs",
        nargs="+",
        help="Profile config file names or paths. Defaults to all enabled local profiles.",
    )
    parser.add_argument(
        "--init-db", action="store_true", help="Initialize each configured database and exit."
    )
    parser.add_argument(
        "--skip-export", action="store_true", help="Do not write the daily CSV exports."
    )
    parser.add_argument("--skip-notion", action="store_true", help="Do not sync to Notion.")
    parser.add_argument(
        "--enable-indeed",
        action="store_true",
        help="Enable Indeed for every selected track using each track's LinkedIn search matrix.",
    )
    parser.add_argument(
        "--skip-email",
        action="store_true",
        help="Do not ingest recommendation emails after the online tracks.",
    )
    parser.add_argument(
        "--post-age-days",
        type=run_daily.positive_int,
        help="Override each online track's post-age window in days. For example, 2 pulls postings up to 48 hours old.",
    )
    parser.add_argument(
        "--query",
        action="append",
        dest="search_queries",
        help="Temporarily replace each selected track's search matrix. Repeat for multiple queries.",
    )
    parser.add_argument(
        "--email-config",
        default="",
        help="Email ingest config path. Defaults to <config-dir>/email.toml.",
    )
    parser.add_argument(
        "--email-lookback-days",
        type=int,
        help=(
            "Email ingest lookback window in days. Defaults to --post-age-days, "
            "or the widest configured online freshness window."
        ),
    )
    parser.add_argument(
        "--email-max-messages",
        type=int,
        default=0,
        help="Maximum recent mailbox messages to inspect; 0 means all messages in the window.",
    )
    parser.add_argument(
        "--email-detail-workers",
        type=int,
        default=16,
        help="Parallel workers for fetching email job detail pages.",
    )
    parser.add_argument(
        "--profile-workers",
        type=run_daily.positive_int,
        default=3,
        help="Maximum online profiles to run concurrently. Notion publishing remains serialized.",
    )
    parser.add_argument("--email-folder", help="Override the configured email IMAP folder/label.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    dashboard = LiveRunTable()
    dashboard.start()
    runtime = run_daily.RuntimeServices(
        request_coalescer=RequestCoalescer(),
        linkedin_request_gate=RequestGate(
            max_concurrency=4,
            min_interval_seconds=0.75,
            rate_limit_cooldown_seconds=30,
        ),
        dashboard=dashboard if dashboard.interactive else None,
    )
    try:
        return _execute(args, runtime, dashboard)
    finally:
        dashboard.finish()


def _execute(
    args: argparse.Namespace,
    runtime: run_daily.RuntimeServices,
    dashboard: LiveRunTable,
) -> int:
    overall_status = 0

    config_paths = resolve_config_paths(args)
    calls: list[tuple[Path, list[str]]] = []
    for config_path in config_paths:
        sub_argv = ["--config", str(config_path)]
        if args.init_db:
            sub_argv.append("--init-db")
        if args.skip_export:
            sub_argv.append("--skip-export")
        if args.skip_notion:
            sub_argv.append("--skip-notion")
        if args.enable_indeed:
            sub_argv.append("--enable-indeed")
        if args.post_age_days is not None:
            sub_argv.extend(["--post-age-days", str(args.post_age_days)])
        for query in args.search_queries or []:
            sub_argv.extend(["--query", query])
        calls.append((config_path, sub_argv))

    worker_count = min(args.profile_workers, len(calls))
    if worker_count <= 1:
        statuses = [(path, _invoke_run_daily(argv, runtime)) for path, argv in calls]
    else:
        statuses: list[tuple[Path, int]] = []
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="profile",
        ) as executor:
            future_paths = {
                executor.submit(_invoke_run_daily, argv, runtime): path for path, argv in calls
            }
            for future in as_completed(future_paths):
                statuses.append((future_paths[future], future.result()))

    for _config_path, status in statuses:
        if status != 0:
            overall_status = status
        if status == 2:
            return status

    if not args.init_db and not args.skip_email:
        if dashboard.interactive:
            dashboard.update(
                "All tracks",
                "Email",
                stage="Fetching",
                detail="Reading mailbox",
            )
        email_lookback_days = (
            args.email_lookback_days
            if args.email_lookback_days is not None
            else args.post_age_days or configured_email_lookback_days(config_paths)
        )
        email_argv = [
            "--config",
            str(
                Path(args.email_config).resolve()
                if args.email_config
                else (Path(args.config_dir) / "email.toml").resolve()
            ),
            "--lookback-days",
            str(email_lookback_days),
            "--max-messages",
            str(args.email_max_messages),
            "--detail-workers",
            str(args.email_detail_workers),
            "--skip-status-import",
        ]
        for config_path in config_paths:
            email_argv.extend(["--track-config", str(config_path)])
        if args.email_folder:
            email_argv.extend(["--folder", args.email_folder])
        if args.skip_notion:
            email_argv.append("--skip-notion")
        status = ingest_email_recommendations.main(email_argv)
        if dashboard.interactive:
            dashboard.update(
                "All tracks",
                "Email",
                stage="Done" if status == 0 else "Failed",
                detail="Mailbox ingest complete" if status == 0 else f"Exit {status}",
            )
        if status != 0:
            overall_status = status
        if not args.skip_export and not refresh_exports(config_paths):
            overall_status = overall_status or 1

    return overall_status


def configured_email_lookback_days(config_paths: list[Path]) -> int:
    """Use one conservative mailbox window for all selected profiles."""
    configured_hours: list[int] = []
    for path in config_paths:
        config = load_config(path)
        configured_hours.append(
            max(
                config.project.recent_post_age_hours,
                config.project.bootstrap_post_age_hours,
            )
        )
    if not configured_hours:
        return 1
    return max(1, (max(configured_hours) + 23) // 24)


def _invoke_run_daily(argv: list[str], runtime: run_daily.RuntimeServices) -> int:
    """Call the modern entry point while keeping compatibility test doubles simple."""

    if "runtime" in inspect.signature(run_daily.main).parameters:
        return run_daily.main(argv, runtime=runtime)
    return run_daily.main(argv)


def refresh_exports(config_paths: list[Path]) -> bool:
    """Rewrite cumulative CSVs after email ingestion so the run is complete."""

    succeeded = True
    started_at = datetime.now(UTC)
    for config_path in config_paths:
        if not config_path.is_file():
            continue
        try:
            config = load_config(config_path)
            destination = run_daily.build_daily_export_path(
                export_dir=config.project.export_dir,
                timezone_name=config.project.timezone,
                started_at=started_at,
                file_prefix=config.project.export_filename_prefix,
            )
            run_daily.export_csv(
                Database(config.project.database_path),
                destination,
                config.filters,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR final CSV refresh failed for {config_path}: {exc}", file=sys.stderr)
            succeeded = False
    return succeeded


def resolve_config_paths(args: argparse.Namespace) -> list[Path]:
    if args.config:
        return [Path(args.config).resolve()]

    config_dir = Path(args.config_dir)
    if not args.configs:
        profiles = [
            load_profile_definition(profile_id, config_root=config_dir)
            for profile_id in available_profiles(config_dir)
        ]
        paths = [profile.runtime_config for profile in profiles if profile.enabled]
        if not paths:
            raise ValueError(
                f"No enabled profiles found under {config_dir}. "
                "Create private profile configuration before running --all."
            )
        return paths

    config_names = args.configs
    paths: list[Path] = []
    for config_name in config_names:
        candidate = Path(config_name)
        if candidate.is_absolute() or candidate.parent != Path("."):
            paths.append(candidate.resolve())
        else:
            paths.append((config_dir / candidate).resolve())
    return paths


if __name__ == "__main__":
    raise SystemExit(main())
