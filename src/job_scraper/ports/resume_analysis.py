"""Stable application boundary for private resume analysis."""

from __future__ import annotations

from typing import Protocol


class ResumeAnalyzer(Protocol):
    def analyze(self, text: str) -> dict[str, list[dict[str, object]]]: ...

    def refine(
        self,
        text: str,
        *,
        answers: dict[str, str],
        current_tracks: list[dict[str, object]],
    ) -> dict[str, object]: ...
