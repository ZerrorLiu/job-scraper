from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from job_scraper.config import HttpConfig, SourceConfig
from job_scraper.domain.models import RawJobRecord, SearchWindow


class RateLimiter:
    def __init__(self, config: HttpConfig) -> None:
        self.config = config

    def sleep(self) -> None:
        delay = self.config.base_delay_seconds + random.uniform(0, self.config.jitter_seconds)
        time.sleep(delay)


class BaseCollector(ABC):
    source_name: str

    def __init__(self, http_config: HttpConfig, source_config: SourceConfig) -> None:
        self.http_config = http_config
        self.source_config = source_config
        self.rate_limiter = RateLimiter(http_config)

    def validate_runtime(self) -> None:
        """Fail before collection if this adapter's optional runtime is unavailable."""
        return None

    def fetch_text(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.http_config.max_retries + 1):
            request = Request(url, headers={"User-Agent": self.http_config.user_agent})
            try:
                with urlopen(request, timeout=self.http_config.timeout_seconds) as response:
                    body = response.read().decode("utf-8", errors="replace")
                lowered = body.lower()
                if "captcha" in lowered or "unusual traffic" in lowered:
                    raise RuntimeError(f"{self.source_name} returned a blocking page")
                return body
            except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
                last_error = exc
                if isinstance(exc, HTTPError) and exc.code == 429:
                    raise
                if attempt >= self.http_config.max_retries:
                    break
                time.sleep((attempt + 1) * self.http_config.base_delay_seconds)
        if last_error:
            raise last_error
        raise RuntimeError(f"{self.source_name} fetch failed without an explicit error")

    @abstractmethod
    def collect(self, window: SearchWindow) -> Iterable[RawJobRecord]:
        raise NotImplementedError
