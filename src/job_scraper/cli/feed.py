"""`job-scraper feed` -- serve the downstream screening contract.

Read-only by construction: it opens each profile's store, reads a window, and
writes a document. Nothing here acquires, publishes, or mutates, so a screener
may call it as often as it likes.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from job_scraper.application.screening_feed import build_feed_document, select_screenable
from job_scraper.config import load_config
from job_scraper.configuration import available_profiles, load_profile_definition
from job_scraper.domain.screening_feed import SETTLED_STATUSES, ScreeningFeedRecord
from job_scraper.storage.db import Database


def _write_utf8_stdout(rendered: str) -> None:
    """Emit the document as UTF-8 regardless of the console's codec.

    Job descriptions carry euro signs, umlauts and CJK, and a Windows console
    defaults to a legacy codec -- `print` there raises UnicodeEncodeError and
    takes the whole command down. Writing bytes to the underlying buffer keeps
    the output identical on every platform, which matters because a downstream
    reader parses it. `buffer` is absent when stdout has been replaced by a
    text-only capture, so fall back to the plain path for that case.
    """
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(rendered)
        return
    buffer.write(rendered.encode("utf-8") + b"\n")
    buffer.flush()


def resolve_window(timezone_name: str, since_days: int, now: datetime) -> tuple[datetime, datetime]:
    """The window a `--since-days N` request means.

    Counted in whole local days rather than as `now - N*24h`, because a daily
    screener asking for "yesterday" wants yesterday's calendar day regardless of
    the hour the run happens to start. `first_seen_at` is stored in UTC, so both
    ends come back in UTC.
    """
    local_now = now.astimezone(ZoneInfo(timezone_name))
    start_of_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    since_local = start_of_today - timedelta(days=max(since_days - 1, 0))
    until_local = start_of_today + timedelta(days=1)
    return since_local.astimezone(UTC), until_local.astimezone(UTC)


def resolve_date_window(
    timezone_name: str, since_date: date, until_date: date | None
) -> tuple[datetime, datetime]:
    """The window an explicit `--since-date`/`--until-date` request means.

    `until_date` is inclusive, matching how an operator reads "from the 24th to
    the 26th", so the half-open end is the start of the following day. Omitting
    it means that single day.
    """
    zone = ZoneInfo(timezone_name)
    end_date = until_date or since_date
    if end_date < since_date:
        raise ValueError(f"--until-date {end_date} is before --since-date {since_date}")
    since_local = datetime.combine(since_date, time.min, zone)
    until_local = datetime.combine(end_date + timedelta(days=1), time.min, zone)
    return since_local.astimezone(UTC), until_local.astimezone(UTC)


def emit_screening_feed(
    profile_ids: list[str] | None,
    *,
    since_days: int,
    published_only: bool,
    include_settled: bool,
    output: Path | None,
    since_date: date | None = None,
    until_date: date | None = None,
    now: datetime | None = None,
) -> int:
    generated_at = now or datetime.now(UTC)
    if profile_ids:
        definitions = [load_profile_definition(profile_id) for profile_id in profile_ids]
    else:
        definitions = [
            definition
            for candidate in available_profiles()
            if (definition := load_profile_definition(candidate)).enabled
        ]
    if not definitions:
        print("No enabled profiles found.")
        return 2

    records: list[ScreeningFeedRecord] = []
    window: tuple[datetime, datetime] | None = None
    for definition in definitions:
        config = load_config(definition.runtime_config)
        if since_date is not None:
            since, until = resolve_date_window(config.project.timezone, since_date, until_date)
        else:
            since, until = resolve_window(config.project.timezone, since_days, generated_at)
        # Profiles may in principle carry different timezones. The document
        # reports one window, so the widest span any profile asked for is the
        # honest answer rather than whichever profile happened to sort last.
        window = (
            (since, until) if window is None else (min(window[0], since), max(window[1], until))
        )
        # Checked before constructing `Database`, whose __init__ creates the
        # parent directory. A read-only command must not leave a tree behind
        # for a profile that has never run.
        if not config.project.database_path.exists():
            continue
        records.extend(
            Database(config.project.database_path).read_screening_feed(
                profile_id=definition.profile_id,
                since=since,
                until_exclusive=until,
            )
        )

    assert window is not None
    selected = select_screenable(
        records,
        published_only=published_only,
        excluded_statuses=() if include_settled else SETTLED_STATUSES,
    )
    document = build_feed_document(
        selected,
        since=window[0],
        until=window[1],
        generated_at=generated_at,
    )
    rendered = json.dumps(document, ensure_ascii=False, indent=2)
    if output is None:
        _write_utf8_stdout(rendered)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {document['record_count']} record(s) to {output}")
    return 0
