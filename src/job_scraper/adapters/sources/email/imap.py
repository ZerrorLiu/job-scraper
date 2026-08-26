from __future__ import annotations

from job_scraper.config import HttpConfig
from job_scraper.integrations.email_recommendations import EmailIngestConfig


class ImapEmailChannel:
    """Declares that a profile takes candidates from a recommendation mailbox.

    Reading the mailbox itself belongs to `jobs.ingest_email_recommendations`,
    which owns the pieces a correct ingest needs: persisted per-message state,
    routing across every track that shares the mailbox, and detail resolution.
    This type used to carry a second, simpler `read()` implementation that the
    production path never called and whose deduplication was reset on every
    call -- exactly the kind of near-copy that silently diverges. What remains
    is the part that is genuinely a component concern: the channel's stable ID
    and its runtime preflight.
    """

    channel_id = "email_imap"

    def __init__(self, config: EmailIngestConfig, http: HttpConfig) -> None:
        self._config = config
        self._http = http

    def validate_runtime(self) -> None:
        missing: list[str] = []
        if not self._config.host:
            missing.append("host")
        if not self._config.username:
            missing.append(self._config.username_env or "username")
        if not self._config.password:
            missing.append(self._config.password_env or "password")
        if missing:
            raise RuntimeError("email_imap is missing configuration: " + ", ".join(missing))
