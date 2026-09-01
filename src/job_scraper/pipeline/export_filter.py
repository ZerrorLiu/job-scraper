"""Export no longer re-derives a content verdict for a stored row.

The acquisition pipeline used to reject on keyword/regex policy (role terms,
excluded terms, employment scope, language ratio, country match), and this
module re-ran the same steps against a stored row so a policy change was
reflected in the next cumulative CSV export. That policy no longer exists --
relevance is now judged by the downstream agent screener, not by keyword
matching -- so there is nothing left to re-derive here. A row that was
persisted stays in the cumulative export.
"""

from __future__ import annotations

from typing import Protocol


class ExportRow(Protocol):
    def __getitem__(self, key: str) -> object: ...

    def keys(self) -> list[str]: ...


def export_row_matches_policy(row: ExportRow, policy: object) -> bool:
    del row, policy
    return True
