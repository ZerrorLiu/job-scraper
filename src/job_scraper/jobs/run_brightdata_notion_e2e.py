from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

from job_scraper.config import AppConfig, load_config
from job_scraper.integrations.notion import NotionClient
from job_scraper.jobs import run_daily

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "e2e_brightdata_notion.db"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one live Indeed term through Bright Data, RawJobRecord normalization, "
            "filtering, SQLite deduplication, and Notion synchronization."
        )
    )
    parser.add_argument("--term", required=True, help="Single Indeed job search term.")
    parser.add_argument("--location", required=True, help="Single Indeed search location.")
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=3,
        help="Maximum Bright Data results to ingest. Defaults to 3.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Track configuration whose filters and Notion layout should be used.",
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="Isolated SQLite database used by the E2E test.",
    )
    parser.add_argument(
        "--respect-post-age",
        action="store_true",
        help="Apply the track's normal post-age filter instead of disabling it for the smoke test.",
    )
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def configure_single_indeed_path(
    config: AppConfig,
    *,
    term: str,
    location: str,
    limit: int,
    database_path: Path,
) -> None:
    normalized_term = term.strip()
    normalized_location = location.strip()
    if not normalized_term:
        raise ValueError("--term must not be empty")
    if not normalized_location:
        raise ValueError("--location must not be empty")

    config.project.database_path = database_path
    for settings in config.sources.values():
        settings.enabled = False
    indeed = config.sources["indeed_brightdata"]
    indeed.enabled = True
    indeed.search_queries = [normalized_term]
    indeed.locations = [normalized_location]
    indeed.max_listing_pages = 1
    indeed.max_detail_fetches = limit


def count_synced_notion_rows(database_path: Path) -> int:
    if not database_path.is_file():
        return 0
    try:
        with sqlite3.connect(database_path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM notion_sync_state WHERE sync_status = 'synced'"
            ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    config_path = Path(args.config).resolve()
    database_path = Path(args.database).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    config = load_config(config_path)
    configure_single_indeed_path(
        config,
        term=args.term,
        location=args.location,
        limit=args.limit,
        database_path=database_path,
    )

    notion = NotionClient(config.notion)
    if not notion.enabled():
        print(
            "E2E preflight failed: Notion is not configured. "
            "Check NOTION_INTEGRATION_TOKEN and the configured Notion target.",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    before_synced = count_synced_notion_rows(database_path)
    print(
        "Starting live E2E test | "
        f"Term: {args.term!r} | Location: {args.location!r} | Limit: {args.limit} | "
        f"Database: {database_path}"
    )

    original_load_config = run_daily.load_config
    run_daily.load_config = lambda _: config
    daily_args = ["--config", str(config_path), "--skip-export"]
    if not args.respect_post_age:
        daily_args.append("--ignore-post-age")
    try:
        status = run_daily.main(daily_args)
    finally:
        run_daily.load_config = original_load_config

    if status != 0:
        print(f"E2E failed: run_daily returned status {status}.", file=sys.stderr)
        return status

    after_synced = count_synced_notion_rows(database_path)
    if after_synced == 0:
        print(
            "E2E failed: Bright Data completed, but no job reached a successful Notion sync. "
            "Review the filter rejection logs or try a broader term/location.",
            file=sys.stderr,
        )
        return 3

    if after_synced > before_synced:
        notion_result = f"created {after_synced - before_synced} new Notion-linked row(s)"
    else:
        notion_result = "confirmed existing Notion-linked rows; duplicate creation was prevented"
    print(f"E2E passed: Bright Data → filtering/SQLite dedupe → Notion; {notion_result}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
