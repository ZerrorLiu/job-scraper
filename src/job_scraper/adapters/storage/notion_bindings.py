from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True)
class NotionDatabaseBinding:
    database_id: str
    data_source_id: str

    @classmethod
    def from_database(cls, database: dict[str, Any]) -> NotionDatabaseBinding:
        database_id = str(database.get("id", "")).strip()
        data_sources = database.get("data_sources", [])
        first_data_source = (
            data_sources[0] if isinstance(data_sources, list) and data_sources else {}
        )
        data_source_id = (
            str(first_data_source.get("id", "")).strip()
            if isinstance(first_data_source, dict)
            else ""
        )
        if not database_id or not data_source_id:
            raise ValueError("Notion database response must include database and data source IDs")
        return cls(database_id=database_id, data_source_id=data_source_id)


REPLACE_ATTEMPTS = 5
REPLACE_BACKOFF_SECONDS = 0.05


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Move `source` onto `destination`, retrying while something else holds it.

    The write is atomic by way of `os.replace`, which on POSIX succeeds even
    when another reader has the destination open. On Windows it does not: any
    open handle -- including one a virus scanner or search indexer takes on a
    file the moment it is created -- fails the call with a permission error.
    Our own code holds neither file open by this point (both reads and the
    temporary write close before this runs), so the holder is external and the
    wait is short.

    Retrying is therefore correct rather than a mask: the operation is
    idempotent, the obstruction is transient, and the alternative is losing a
    binding write for a reason that has nothing to do with the caller. After
    the last attempt the error is raised unchanged, because a permission error
    that outlasts the backoff is a real one.
    """

    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_BACKOFF_SECONDS * (attempt + 1))


class NotionDatabaseBindingStore:
    """Private, atomic per-profile binding state for daily Notion databases."""

    _locks_guard: ClassVar[Lock] = Lock()
    _locks: ClassVar[dict[Path, RLock]] = {}

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, profile_id: str) -> NotionDatabaseBinding | None:
        entry = self._entries().get(profile_id)
        if not isinstance(entry, dict):
            return None
        database_id = str(entry.get("database_id", "")).strip()
        data_source_id = str(entry.get("data_source_id", "")).strip()
        if not database_id or not data_source_id:
            return None
        return NotionDatabaseBinding(database_id=database_id, data_source_id=data_source_id)

    def save(self, profile_id: str, binding: NotionDatabaseBinding) -> None:
        normalized_profile_id = profile_id.strip()
        if not normalized_profile_id:
            raise ValueError("profile_id is required for a Notion database binding")
        with self._write_lock():
            entries = self._entries()
            entries[normalized_profile_id] = {
                "database_id": binding.database_id,
                "data_source_id": binding.data_source_id,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "tracks": entries}
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=f".{self.path.stem}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    json.dump(payload, handle, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary_path = Path(handle.name)
                _replace_with_retry(temporary_path, self.path)
            finally:
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink()

    def _write_lock(self) -> RLock:
        key = self.path.resolve()
        with self._locks_guard:
            return self._locks.setdefault(key, RLock())

    def _entries(self) -> dict[str, object]:
        if not self.path.is_file():
            return {}
        try:
            with self.path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read Notion binding state {self.path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Notion binding state {self.path} must be a JSON object")
        tracks = payload.get("tracks", {})
        if not isinstance(tracks, dict):
            raise ValueError(f"Notion binding state {self.path} has an invalid tracks object")
        return dict(tracks)
