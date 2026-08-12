from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from job_scraper.application.search_planner import build_search_plan, load_watchlists
from job_scraper.cli.bootstrap import (
    DEFAULT_PIPELINE,
    BootstrapRequest,
    initialize_profile,
)
from job_scraper.cli.database import (
    initialize_profiles,
    migrate_profiles,
    resolve_status_workspace_path,
    show_status,
)
from job_scraper.cli.doctor import has_errors, run_doctor
from job_scraper.config import load_config
from job_scraper.configuration import (
    available_profiles,
    get_config_root,
    load_profile_definition,
)
from job_scraper.configuration.composition import apply_profile_to_runtime
from job_scraper.jobs import (
    ingest_email_recommendations,
    run_all_tracks,
)
from job_scraper.registry.builtins import (
    create_builtin_registry,
    validate_component_ids,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-scraper",
        description="Composable job discovery pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser(
        "init",
        help="Generate a private, runnable profile workspace without storing secrets.",
    )
    init.add_argument("--profile-id", required=True)
    init.add_argument("--label", required=True)
    init.add_argument("--query", action="append", required=True, dest="queries")
    init.add_argument("--location", action="append", required=True, dest="locations")
    init.add_argument("--country", action="append", required=True, dest="countries")
    init.add_argument("--keyword", action="append", required=True, dest="keywords")
    init.add_argument(
        "--source",
        action="append",
        required=True,
        dest="sources",
    )
    init.add_argument(
        "--sink",
        action="append",
        dest="sinks",
    )
    init.add_argument("--channel", action="append", dest="channels")
    init.add_argument("--step", action="append", dest="pipeline")
    init.add_argument("--email", action="store_true")
    init.add_argument("--imap-host", default="")
    init.add_argument("--timezone", default="UTC")
    init.add_argument("--config-dir", type=Path)

    doctor = subparsers.add_parser("doctor", help="Check runtime and configuration.")
    doctor.add_argument("--profile", help="Check only this profile.")
    doctor.add_argument(
        "--all",
        action="store_true",
        help="Check every local profile explicitly. This is also the default when "
        "neither --profile nor --all is given.",
    )

    subparsers.add_parser("list", help="List available profiles.")
    capabilities = subparsers.add_parser(
        "capabilities",
        help="List components and credential environment variables for an Agent.",
    )
    capabilities.add_argument("--json", action="store_true")
    plan = subparsers.add_parser("plan", help="Preview the deduplicated search plan.")
    plan.add_argument("--show-queries", action="store_true")

    config_parser = subparsers.add_parser("config", help="Configuration commands.")
    config_subparsers = config_parser.add_subparsers(
        dest="config_command",
        required=True,
    )
    validate = config_subparsers.add_parser("validate")
    validate.add_argument("--profile", help="Validate only this profile.")
    validate.add_argument(
        "--all",
        action="store_true",
        help="Validate every local profile explicitly. This is also the default when "
        "neither --profile nor --all is given.",
    )

    run = subparsers.add_parser(
        "run",
        help="Run all enabled profiles, or one profile selected with --profile.",
    )
    run.add_argument(
        "--profile",
        help="Run only this profile. By default all enabled profiles run.",
    )
    run.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--init-db", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--enable-indeed", action="store_true", help=argparse.SUPPRESS)
    run.add_argument(
        "--skip-email",
        action="store_true",
        help="Skip configured recommendation-email ingestion.",
    )
    run.add_argument(
        "--skip-notion",
        action="store_true",
        help="Skip configured Notion publishing.",
    )
    run.add_argument(
        "--skip-export",
        action="store_true",
        help="Skip cumulative CSV exports.",
    )
    run.add_argument(
        "--post-age-days",
        type=int,
        help="Temporarily override the configured freshness window.",
    )
    run.add_argument(
        "--profile-workers",
        type=int,
        default=3,
        help="Maximum profiles to run concurrently (default: 3).",
    )
    run.add_argument(
        "--query",
        action="append",
        help="Temporarily replace every selected profile's search matrix; repeatable.",
    )

    email = subparsers.add_parser(
        "ingest-email",
        help="Read and route recommendation emails.",
    )
    email.add_argument("arguments", nargs=argparse.REMAINDER)

    database = subparsers.add_parser("db", help="Workspace database commands.")
    database_subparsers = database.add_subparsers(
        dest="database_command",
        required=True,
    )
    initialize = database_subparsers.add_parser(
        "init",
        help="Initialize operational databases without running acquisition.",
    )
    initialize.add_argument(
        "--profile",
        help="Initialize only this profile. By default all enabled profiles are initialized.",
    )
    migrate = database_subparsers.add_parser("migrate")
    migrate.add_argument(
        "--profile",
        action="append",
        dest="profiles",
        help="Migrate only this profile. Repeatable. By default every local profile is migrated.",
    )
    migrate.add_argument(
        "--workspace",
        type=Path,
        help="Destination workspace database path. Defaults to the path the selected "
        "profiles' runtime config agrees on. Must not match a source profile's own "
        "database.",
    )
    migrate.add_argument("--dry-run", action="store_true")
    status = database_subparsers.add_parser("status")
    status.add_argument(
        "--profile",
        action="append",
        dest="profiles",
        help="Resolve the workspace database from this profile's runtime config. "
        "Repeatable. By default every local profile is considered.",
    )
    status.add_argument(
        "--workspace",
        type=Path,
        help="Inspect this workspace database path directly instead of resolving it "
        "from local profiles.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    raw_argv = list(argv) if argv is not None else list(sys.argv[1:])
    if raw_argv and raw_argv[0] == "ingest-email":
        return ingest_email_recommendations.main(raw_argv[1:])
    args = build_parser().parse_args(raw_argv)
    if args.command == "init":
        return _initialize(args)
    if args.command == "doctor":
        return _doctor(args.profile, all_profiles=args.all)
    if args.command == "list":
        return _list_profiles()
    if args.command == "capabilities":
        return _show_capabilities(as_json=args.json)
    if args.command == "plan":
        return _show_search_plan(
            show_queries=args.show_queries,
        )
    if args.command == "config":
        return _validate_config(args.profile, all_profiles=args.all)
    if args.command == "run":
        return _run(args)
    # "ingest-email" is handled by the raw_argv[0] check above, before
    # build_parser().parse_args() ever runs, so args.command == "ingest-email"
    # is unreachable here. The subparser above stays registered only so
    # `job-scraper --help` still lists it as an available command.
    if args.command == "db":
        if args.database_command == "init":
            return initialize_profiles(args.profile)
        if args.database_command == "migrate":
            return migrate_profiles(
                args.profiles,
                workspace_path=args.workspace,
                dry_run=args.dry_run,
            )
        if args.database_command == "status":
            return show_status(resolve_status_workspace_path(args.profiles, args.workspace))
    raise AssertionError(f"Unhandled command: {args.command}")


def _initialize(args: argparse.Namespace) -> int:
    try:
        result = initialize_profile(
            BootstrapRequest(
                config_root=args.config_dir or get_config_root(),
                profile_id=args.profile_id,
                label=args.label,
                queries=tuple(args.queries),
                locations=tuple(args.locations),
                countries=tuple(args.countries),
                keywords=tuple(args.keywords),
                sources=tuple(args.sources),
                channels=tuple(args.channels or ()),
                pipeline=tuple(args.pipeline or DEFAULT_PIPELINE),
                sinks=tuple(args.sinks or ("csv",)),
                enable_email=args.email,
                timezone=args.timezone,
                imap_host=args.imap_host,
            )
        )
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    print(f"CREATED profile: {result.profile_path}")
    print(f"CREATED runtime: {result.runtime_path}")
    if result.email_path is not None:
        print(f"READY email: {result.email_path}")
    print("NEXT copy .env.example to .env, fill enabled credentials, then run doctor --all")
    return 0


def _doctor(profile_id: str | None, *, all_profiles: bool) -> int:
    selected = _selected_profile_ids(profile_id, all_profiles=all_profiles)
    if not selected:
        return 2
    failed = False
    for selected_id in selected:
        print(f"[{selected_id}]")
        results = run_doctor(selected_id)
        for result in results:
            print(f"{result.status.upper():7} {result.name}: {result.message}")
        failed = failed or has_errors(results)
    print(f"Checked {len(selected)} profile(s): {', '.join(selected)}")
    return 1 if failed else 0


def _list_profiles() -> int:
    for profile_id in available_profiles():
        profile = load_profile_definition(profile_id)
        status = "enabled" if profile.enabled else "disabled"
        print(f"{profile.profile_id:20} {profile.label:20} {status:8}")
    return 0


def _show_capabilities(*, as_json: bool) -> int:
    registry = create_builtin_registry()
    components = {
        "sources": list(registry.sources.available()),
        "channels": list(registry.channels.available()),
        "pipeline_steps": list(registry.steps.available()),
        "sinks": list(registry.sinks.available()),
    }
    payload = {
        **components,
        "credential_environment": {
            "indeed_brightdata": [
                "BRIGHTDATA_API_KEY",
                "BRIGHTDATA_DATASET_ID",
            ],
            "email_imap": [
                "JOB_EMAIL_USERNAME",
                "JOB_EMAIL_APP_PASSWORD",
            ],
            "notion_daily": [
                "NOTION_INTEGRATION_TOKEN",
                "NOTION_PARENT_PAGE_ID",
            ],
        },
    }
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    for kind, values in components.items():
        print(f"{kind}: {', '.join(values) or '(none)'}")
    return 0


def _show_search_plan(*, show_queries: bool) -> int:
    profiles = [load_profile_definition(profile_id) for profile_id in available_profiles()]
    plan = build_search_plan(
        profiles,
        load_watchlists(get_config_root()),
    )
    print(f"Unique search intents: {len(plan.intents)}")
    for profile in profiles:
        intents = plan.for_profile(profile.profile_id)
        if not intents:
            continue
        print(f"{profile.profile_id:20} {len(intents):3} intents")
    if show_queries:
        for intent in plan.intents:
            profiles_text = ",".join(intent.profile_ids)
            print(f"{intent.query} | profiles={profiles_text}")
    return 0


def _validate_config(profile_id: str | None, *, all_profiles: bool) -> int:
    selected = _selected_profile_ids(profile_id, all_profiles=all_profiles)
    if not selected:
        return 2
    status = 0
    for selected_id in selected:
        try:
            profile = load_profile_definition(selected_id)
            config = load_config(profile.runtime_config)
            apply_profile_to_runtime(profile, config)
            validate_component_ids(
                create_builtin_registry(),
                sources=profile.sources,
                channels=profile.channels,
                steps=profile.pipeline,
                sinks=profile.sinks,
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            print(f"ERROR {selected_id}: {exc}", file=sys.stderr)
            status = 2
            continue
        print(f"OK {profile.profile_id}: {profile.runtime_config}")
    print(f"Checked {len(selected)} profile(s): {', '.join(selected)}")
    return status


def _run(args: argparse.Namespace) -> int:
    if args.all and args.profile:
        print("ERROR choose either --all or --profile", file=sys.stderr)
        return 2
    common = _legacy_run_flags(args)
    if args.profile is None:
        profiles = [
            profile
            for profile_id in available_profiles()
            if (profile := load_profile_definition(profile_id)).enabled
        ]
        config_paths = [str(profile.runtime_config) for profile in profiles]
        if not config_paths:
            print(
                "ERROR no enabled local profiles found; create private configuration first",
                file=sys.stderr,
            )
            return 2
        common.extend(
            [
                "--config-dir",
                str(get_config_root()),
                "--configs",
                *config_paths,
                "--profile-workers",
                str(args.profile_workers),
            ]
        )
        if args.skip_email or not any("email_imap" in profile.channels for profile in profiles):
            common.append("--skip-email")
        return run_all_tracks.main(common)

    profile_id = _selected_profile_id(args.profile)
    if profile_id is None:
        return 2
    try:
        profile = load_profile_definition(profile_id)
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    if args.skip_email or "email_imap" not in profile.channels:
        common.append("--skip-email")
    return run_all_tracks.main(
        [
            "--config-dir",
            str(get_config_root()),
            "--config",
            str(profile.runtime_config),
            *common,
        ]
    )


def _legacy_run_flags(args: argparse.Namespace) -> list[str]:
    flags: list[str] = []
    if args.init_db:
        flags.append("--init-db")
    if args.enable_indeed:
        flags.append("--enable-indeed")
    if args.skip_notion:
        flags.append("--skip-notion")
    if args.skip_export:
        flags.append("--skip-export")
    if args.post_age_days is not None:
        flags.extend(["--post-age-days", str(args.post_age_days)])
    for query in args.query or []:
        flags.extend(["--query", query])
    return flags


def _selected_profile_id(requested: str | None) -> str | None:
    if requested:
        return requested
    profiles = available_profiles()
    if profiles:
        return profiles[0]
    print(
        "ERROR no local profiles found; set JOB_SCRAPER_CONFIG_DIR or create ./config/profiles",
        file=sys.stderr,
    )
    return None


def _selected_profile_ids(
    requested: str | None,
    *,
    all_profiles: bool,
) -> tuple[str, ...]:
    if requested and all_profiles:
        print("ERROR choose either --profile or --all", file=sys.stderr)
        return ()
    if all_profiles or requested is None:
        # No --profile and no --all: check every local profile by default,
        # matching `run`'s default of running all enabled profiles.
        # Silently checking only the alphabetically-first profile would
        # produce a misleadingly clean report while other profiles go
        # unchecked.
        profiles = available_profiles()
        if profiles:
            return profiles
        print(
            "ERROR no local profiles found; set JOB_SCRAPER_CONFIG_DIR or run job-scraper init",
            file=sys.stderr,
        )
        return ()
    return (requested,)


if __name__ == "__main__":
    raise SystemExit(main())
