from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from job_scraper.application.acquisition import RequestCoalescer, RequestGate
from job_scraper.application.run_plan import ProfileRunRequest, build_daily_export_path
from job_scraper.cli.console import LiveRunTable, display_track_label
from job_scraper.config import load_config
from job_scraper.configuration import (
    available_profiles,
    get_config_root,
    load_profile_definition,
)
from job_scraper.jobs import ingest_email_recommendations, run_daily
from job_scraper.storage.db import Database


@dataclass(frozen=True, slots=True)
class AllTracksRequest:
    """Typed orchestration input shared by the public CLI and scheduler entry point."""

    config_paths: tuple[Path, ...]
    config_dir: Path
    skip_export: bool = False
    skip_notion: bool = False
    skip_email: bool = False
    post_age_days: int | None = None
    search_queries: tuple[str, ...] = ()
    only_sources: tuple[str, ...] = ()
    email_config: str = ""
    email_lookback_days: int | None = None
    email_max_messages: int = 0
    email_detail_workers: int = 16
    profile_workers: int = 4
    email_folder: str | None = None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all configured daily job tracks.", allow_abbrev=False
    )
    parser.add_argument(
        "--config-dir",
        # Resolved through the shared helper so the documented
        # JOB_SCRAPER_CONFIG_DIR is actually honoured. Hard-coding the repo's
        # own ./config here made the env var silently ineffective for anyone
        # invoking this module directly.
        default=str(get_config_root()),
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
        "--skip-export", action="store_true", help="Do not write the daily CSV exports."
    )
    parser.add_argument("--skip-notion", action="store_true", help="Do not sync to Notion.")
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
        "--source",
        action="append",
        dest="only_sources",
        help=(
            "Run only these of each selected track's sources. Repeat to select "
            "several. Use when one source belongs on a different schedule."
        ),
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
        default=4,
        help="Maximum online profiles to run concurrently. Notion publishing remains serialized.",
    )
    parser.add_argument("--email-folder", help="Override the configured email IMAP folder/label.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    request = AllTracksRequest(
        config_paths=tuple(resolve_config_paths(args)),
        config_dir=Path(args.config_dir),
        skip_export=args.skip_export,
        skip_notion=args.skip_notion,
        skip_email=args.skip_email,
        post_age_days=args.post_age_days,
        search_queries=tuple(args.search_queries or ()),
        only_sources=tuple(args.only_sources or ()),
        email_config=args.email_config,
        email_lookback_days=args.email_lookback_days,
        email_max_messages=args.email_max_messages,
        email_detail_workers=args.email_detail_workers,
        profile_workers=args.profile_workers,
        email_folder=args.email_folder,
    )
    return execute(request)


def execute(request: AllTracksRequest) -> int:
    """Run the configured profiles without reparsing a second command line."""
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
        return _execute(request, runtime, dashboard)
    finally:
        dashboard.finish()


def _execute(
    request: AllTracksRequest,
    runtime: run_daily.RuntimeServices,
    dashboard: LiveRunTable,
) -> int:
    overall_status = 0

    config_paths = list(request.config_paths)
    if dashboard.interactive and not request.skip_email:
        for config_path in config_paths:
            config = load_config(config_path)
            dashboard.update(
                display_track_label(config.project.track_label),
                "Email",
                stage="Waiting",
                progress_text="Starting",
                detail="Starting mailbox",
            )
    email_request = build_email_request(request, config_paths)
    email_executor: ThreadPoolExecutor | None = None
    email_future = None
    if email_request is not None:
        email_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="email-prepare")
        email_future = email_executor.submit(
            _invoke_email_prepare,
            email_request,
            dashboard if dashboard.interactive else None,
        )
    calls = [
        (config_path, build_profile_request(request, config_path)) for config_path in config_paths
    ]

    try:
        worker_count = min(request.profile_workers, len(calls))
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

        if email_future is not None:
            try:
                preparation = email_future.result()
            except Exception as exc:
                dashboard.record_message(f"Email | Failed | {exc}", error=True)
                overall_status = overall_status or 1
            else:
                status = ingest_email_recommendations.finish(
                    preparation,
                    dashboard=dashboard if dashboard.interactive else None,
                )
                if status != 0:
                    overall_status = status
            if not request.skip_export and not refresh_exports(config_paths):
                overall_status = overall_status or 1

        return overall_status
    finally:
        if email_executor is not None:
            email_executor.shutdown(wait=True, cancel_futures=True)


def build_profile_request(request: AllTracksRequest, config_path: Path) -> ProfileRunRequest:
    """Translate all-profile options once into the typed per-profile boundary."""

    return ProfileRunRequest(
        config_path=config_path,
        skip_export=request.skip_export,
        skip_notion=request.skip_notion,
        post_age_days=request.post_age_days,
        search_queries=request.search_queries,
        only_sources=request.only_sources,
    )


def build_email_request(
    request: AllTracksRequest,
    config_paths: list[Path],
) -> ingest_email_recommendations.EmailPrepareRequest | None:
    if request.skip_email:
        return None
    email_lookback_days = (
        request.email_lookback_days
        if request.email_lookback_days is not None
        else request.post_age_days or configured_email_lookback_days(config_paths)
    )
    return ingest_email_recommendations.EmailPrepareRequest(
        config_path=(
            Path(request.email_config).resolve()
            if request.email_config
            else (request.config_dir / "email.toml").resolve()
        ),
        track_config_paths=tuple(config_paths),
        lookback_days=email_lookback_days,
        max_messages=request.email_max_messages,
        detail_workers=request.email_detail_workers,
        folder=request.email_folder,
        skip_notion=request.skip_notion,
    )


def _invoke_email_prepare(
    request: ingest_email_recommendations.EmailPrepareRequest,
    dashboard: LiveRunTable | None,
) -> ingest_email_recommendations.EmailPreparation:
    return ingest_email_recommendations.prepare_request(request, dashboard=dashboard)


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


def _invoke_run_daily(request: ProfileRunRequest, runtime: run_daily.RuntimeServices) -> int:
    return run_daily.execute(request, runtime=runtime)


def refresh_exports(config_paths: list[Path]) -> bool:
    """Rewrite cumulative CSVs after email ingestion so the run is complete."""

    succeeded = True
    started_at = datetime.now(UTC)
    for config_path in config_paths:
        if not config_path.is_file():
            continue
        try:
            config = load_config(config_path)
            destination = build_daily_export_path(
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
