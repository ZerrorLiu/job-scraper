from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from job_scraper.application.application_runtime import ApplicationRuntime


class SessionStateError(ValueError):
    """Raised when private browser session state is invalid or unsafe."""


@dataclass(frozen=True, slots=True)
class ApplicationSessionState:
    """The resumable, non-secret checkpoint for one browser session."""

    status: str
    canonical_job_id: str
    platform: str
    requested_url: str
    current_url: str
    step: str
    human_action: str = ""

    def with_update(self, **changes: str) -> ApplicationSessionState:
        values = asdict(self)
        values.update(changes)
        return ApplicationSessionState(**values)


def load_session_state(runtime: ApplicationRuntime) -> ApplicationSessionState | None:
    path = runtime.session_state_file
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SessionStateError(f"Invalid browser session state: {path}") from exc
    if not isinstance(payload, dict):
        raise SessionStateError("Browser session state must be a JSON object")
    try:
        state = ApplicationSessionState(
            status=_text(payload, "status"),
            canonical_job_id=_text(payload, "canonical_job_id"),
            platform=_text(payload, "platform"),
            requested_url=_text(payload, "requested_url"),
            current_url=_text(payload, "current_url"),
            step=_text(payload, "step"),
            human_action=_text(payload, "human_action", required=False),
        )
    except KeyError as exc:
        raise SessionStateError(f"Missing browser session field: {exc.args[0]}") from exc
    return state


def save_session_state(
    runtime: ApplicationRuntime,
    state: ApplicationSessionState,
) -> None:
    runtime.root.mkdir(parents=True, exist_ok=True)
    payload = asdict(state)
    payload["updated_at"] = datetime.now(UTC).isoformat()
    runtime.session_state_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _text(payload: dict[str, object], key: str, *, required: bool = True) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise SessionStateError(f"Browser session field must be text: {key}")
    if required and not value.strip():
        raise KeyError(key)
    return value.strip()
