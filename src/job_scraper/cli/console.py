from __future__ import annotations

import os
import sys
import time
from collections import Counter, OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import BinaryIO, TextIO, cast

_active_dashboard: LiveRunTable | None = None
_dashboard_lock = RLock()


def timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log_line(message: str) -> None:
    dashboard = active_dashboard()
    if dashboard is not None:
        dashboard.record_message(message)
        return
    emit_line(message, sys.stdout)


def log_error(message: str) -> None:
    dashboard = active_dashboard()
    if dashboard is not None:
        dashboard.record_message(message, error=True)
        return
    emit_line(message, sys.stderr)


def emit_line(message: str, stream: TextIO) -> None:
    line = f"[{timestamp()}] {message}\n"
    try:
        stream.write(line)
        stream.flush()
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        buffer = cast(BinaryIO | None, getattr(stream, "buffer", None))
        if buffer is not None:
            buffer.write(line.encode(encoding, errors="replace"))
            buffer.flush()
            return
        stream.write(line.encode("ascii", errors="replace").decode("ascii"))
        stream.flush()


def source_label(value: str) -> str:
    labels = {
        "linkedin": "LinkedIn",
        "indeed": "Indeed",
    }
    return labels.get(value, value.capitalize())


def format_reasons(reject_counts: Counter[str]) -> str:
    if not reject_counts:
        return "none"
    return ", ".join(
        f"{reason_label(reason)} {count}" for reason, count in reject_counts.most_common(3)
    )


def reason_label(reason: str) -> str:
    labels = {
        "not_target_country": "outside target countries",
        "older_than_24h": "older than post-age window",
        "company_not_allowed": "company not allowed",
        "missing_target_keywords": "missing target keywords",
        "excluded_keyword": "excluded keyword",
        "excluded_requirement": "excluded requirement",
        "non_english": "non-English",
        "not_full_time": "not full-time",
        "already_seen": "already seen",
        "already_processed": "already processed",
        "detail_unavailable": "detail unavailable",
    }
    return labels.get(reason, reason)


def log_job_status(
    source: str,
    seen_index: int,
    decision: str,
    reason: str,
    title: str,
    company: str,
    city: str,
) -> None:
    source_text = pad(source_label(source), 9)
    seen_text = str(seen_index).rjust(4)
    decision_text = pad(decision, 8)
    reason_text = pad(reason or "-", 16)
    title_text = pad(compact_text(title, 52), 52)
    company_text = pad(compact_text(company, 26), 26)
    city_text = compact_text(city, 16)
    log_line(
        f"{source_text} #{seen_text} | {decision_text} | {reason_text} | "
        f"{title_text} | {company_text} | {city_text}"
    )


def compact_text(value: str, limit: int) -> str:
    cleaned = " ".join((value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        return cleaned[:limit]
    return cleaned[: limit - 1] + "..."


def pad(value: str, width: int) -> str:
    return value.ljust(width)


def display_track_label(value: str) -> str:
    normalized = " ".join((value or "").split()).strip()
    return normalized or "Default"


@dataclass(slots=True)
class _ProgressRow:
    track: str
    source: str
    stage: str = "Waiting"
    keywords_done: int = 0
    keywords_total: int = 0
    progress_text: str = ""
    seen: int = 0
    accepted: int = 0
    filtered: int = 0
    detail: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


class LiveRunTable:
    """Thread-safe ANSI dashboard for one workspace run."""

    def __init__(self, stream: TextIO | None = None, *, force: bool = False) -> None:
        self.stream = stream or sys.stdout
        is_terminal = bool(getattr(self.stream, "isatty", lambda: False)())
        self.interactive = force or (is_terminal and _enable_virtual_terminal(self.stream))
        self._lock = RLock()
        self._rows: OrderedDict[tuple[str, str], _ProgressRow] = OrderedDict()
        self._started_at = time.monotonic()
        self._finished_at = 0.0
        self._screen_active = False
        self._last_render_at = 0.0
        self._last_event = ""
        self._errors: list[str] = []

    def start(self) -> None:
        if not self.interactive:
            return
        global _active_dashboard
        with _dashboard_lock:
            _active_dashboard = self
        self.stream.write("\x1b[?1049h\x1b[H\x1b[?25l")
        self.stream.flush()
        self._screen_active = True
        self._render(force=True)

    def update(
        self,
        track: str,
        source: str,
        *,
        stage: str | None = None,
        keywords_done: int | None = None,
        keywords_total: int | None = None,
        progress_text: str | None = None,
        seen: int | None = None,
        accepted: int | None = None,
        filtered: int | None = None,
        detail: str | None = None,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            key = (track, source)
            row = self._rows.get(key)
            if row is None:
                row = _ProgressRow(track=track, source=source, started_at=now)
                self._rows[key] = row
            if stage is not None:
                row.stage = stage
                if stage in {"Done", "Failed", "Skipped"}:
                    row.finished_at = now
            if keywords_done is not None:
                row.keywords_done = keywords_done
            if keywords_total is not None:
                row.keywords_total = keywords_total
            if progress_text is not None:
                row.progress_text = compact_text(progress_text, 9)
            if seen is not None:
                row.seen = seen
            if accepted is not None:
                row.accepted = accepted
            if filtered is not None:
                row.filtered = filtered
            if detail is not None:
                row.detail = compact_text(detail, 28)
        self._render()

    def record_message(self, message: str, *, error: bool = False) -> None:
        with self._lock:
            self._last_event = compact_text(message, 120)
            if error:
                self._errors.append(message)
        self._render()

    def finish(self) -> None:
        self._finished_at = time.monotonic()
        self._render(force=True)
        if not self.interactive:
            return
        global _active_dashboard
        with _dashboard_lock:
            if _active_dashboard is self:
                _active_dashboard = None
        if self._screen_active:
            self.stream.write("\x1b[?25h\x1b[?1049l")
            self.stream.flush()
            self._screen_active = False
        emit_line(self.render_text(), self.stream)
        totals = self.totals()
        status = "completed with errors" if self._errors else "completed"
        emit_line(
            f"Summary | {status} | Seen {totals['seen']} | Accepted {totals['accepted']} | "
            f"Filtered {totals['filtered']} | Elapsed {_duration(self.elapsed_seconds)}",
            self.stream,
        )
        for error in self._errors:
            emit_line(f"ERROR {error}", self.stream)

    @property
    def elapsed_seconds(self) -> float:
        end = self._finished_at or time.monotonic()
        return max(0.0, end - self._started_at)

    def totals(self) -> dict[str, int]:
        with self._lock:
            return {
                "seen": sum(row.seen for row in self._rows.values()),
                "accepted": sum(row.accepted for row in self._rows.values()),
                "filtered": sum(row.filtered for row in self._rows.values()),
            }

    def render_text(self) -> str:
        with self._lock:
            state = "FINAL" if self._finished_at else "LIVE"
            lines = [f"Job Scraper [{state}]"]
            lines.append(
                f"{'Track':<14} {'Source':<10} {'Stage':<12} {'Progress':>9} "
                f"{'Seen':>6} {'Keep':>6} {'Drop':>6}  Detail"
            )
            lines.append("-" * 92)
            for track, rows in _group_rows_by_track(self._rows.values()):
                for index, row in enumerate(rows):
                    keyword_text = row.progress_text or (
                        f"{row.keywords_done}/{row.keywords_total}" if row.keywords_total else "-"
                    )
                    row_end = row.finished_at or time.monotonic()
                    row_elapsed = _duration(max(0.0, row_end - row.started_at))
                    detail = row.detail or row_elapsed
                    track_cell = compact_text(track, 14) if index == 0 else ""
                    lines.append(
                        f"{track_cell:<14} "
                        f"{compact_text(row.source, 10):<10} "
                        f"{compact_text(row.stage, 12):<12} "
                        f"{keyword_text:>9} {row.seen:>6} {row.accepted:>6} "
                        f"{row.filtered:>6}  {compact_text(detail, 28)}"
                    )
            if not self._rows:
                lines.append("Preparing run...")
            if self._last_event:
                lines.append("-" * 92)
                lines.append(f"Last event: {self._last_event}")
            return "\n".join(lines)

    def _render(self, *, force: bool = False) -> None:
        if not self.interactive:
            return
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_render_at < 0.1:
                return
            rendered = self.render_text()
            self.stream.write("\x1b[H" + rendered + "\x1b[J")
            self.stream.flush()
            self._last_render_at = now


def active_dashboard() -> LiveRunTable | None:
    with _dashboard_lock:
        return _active_dashboard


def _duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, remaining = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{remaining:02d}"
    return f"{minutes:02d}:{remaining:02d}"


def _group_rows_by_track(
    rows: Iterable[_ProgressRow],
) -> list[tuple[str, list[_ProgressRow]]]:
    grouped: OrderedDict[str, list[_ProgressRow]] = OrderedDict()
    for row in rows:
        grouped.setdefault(row.track, []).append(row)
    source_order = {"LinkedIn": 0, "Indeed": 1, "Email": 2}
    for track_rows in grouped.values():
        track_rows.sort(key=lambda row: source_order.get(row.source, 99))
    return list(grouped.items())


def _enable_virtual_terminal(stream: TextIO) -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt

        file_descriptor = stream.fileno()
        console_handle = msvcrt.get_osfhandle(file_descriptor)
        mode = ctypes.c_ulong()
        kernel32 = ctypes.windll.kernel32
        if not kernel32.GetConsoleMode(console_handle, ctypes.byref(mode)):
            return False
        enable_virtual_terminal_processing = 0x0004
        return bool(
            kernel32.SetConsoleMode(
                console_handle,
                mode.value | enable_virtual_terminal_processing,
            )
        )
    except (AttributeError, OSError, ValueError):
        return False
