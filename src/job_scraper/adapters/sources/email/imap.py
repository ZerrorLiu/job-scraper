from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from job_scraper.config import HttpConfig
from job_scraper.domain.models import RawJobRecord
from job_scraper.integrations.email_recommendations import (
    EmailIngestConfig,
    ImapEmailClient,
    canonical_link_key,
    enrich_email_candidate_to_raw_job,
    extract_job_candidates,
    job_platform_from_url,
)


class ImapEmailChannel:
    """Read recommendation emails once and emit platform-aware raw jobs."""

    channel_id = "email_imap"

    def __init__(
        self,
        config: EmailIngestConfig,
        http: HttpConfig,
    ) -> None:
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

    def read(self) -> Iterable[RawJobRecord]:
        self.validate_runtime()
        seen_links: set[str] = set()
        scraped_at = datetime.now(UTC)
        for message in ImapEmailClient(
            self._config,
            timeout_seconds=self._http.timeout_seconds,
        ).fetch_recent_messages():
            for candidate in extract_job_candidates(message):
                link_key = canonical_link_key(candidate.url)
                if link_key in seen_links:
                    continue
                seen_links.add(link_key)
                raw = enrich_email_candidate_to_raw_job(
                    candidate,
                    self._http,
                    scraped_at=scraped_at,
                )
                platform = job_platform_from_url(candidate.url)
                raw.raw_payload["source_platforms"] = [platform]
                raw.raw_payload["acquisition_mode"] = "email"
                yield raw
