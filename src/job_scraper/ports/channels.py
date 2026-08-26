from __future__ import annotations

from typing import Protocol


class JobChannel(Protocol):
    """A non-search candidate stream a profile can declare.

    Unlike a Source, a Channel is not polled by the profile runner: the channel
    id is a declaration that a dedicated job owns that stream (today, the
    recommendation-mailbox ingest). The contract is therefore identity plus
    preflight, not iteration.
    """

    channel_id: str

    def validate_runtime(self) -> None: ...
