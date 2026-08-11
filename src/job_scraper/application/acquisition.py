from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from threading import Lock, Semaphore
from time import monotonic, sleep
from typing import TypeVar, cast

T = TypeVar("T")


class RequestCoalescer:
    """Share identical in-flight and completed acquisition requests within one run."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[str, Future[object]] = {}

    def execute(self, key: str, operation: Callable[[], T]) -> T:
        with self._lock:
            existing = self._requests.get(key)
            if existing is None:
                future: Future[object] = Future()
                self._requests[key] = future
                owns_request = True
            else:
                future = existing
                owns_request = False

        if not owns_request:
            return cast(T, future.result())

        try:
            result = operation()
        except BaseException as exc:
            future.set_exception(exc)
            raise
        future.set_result(result)
        return result

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self._requests)


class RequestGate:
    """Apply one run-wide concurrency and request-start-rate bound."""

    def __init__(
        self,
        *,
        max_concurrency: int,
        min_interval_seconds: float,
        rate_limit_cooldown_seconds: float = 0.0,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be greater than zero")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        if rate_limit_cooldown_seconds < 0:
            raise ValueError("rate_limit_cooldown_seconds cannot be negative")
        self._semaphore = Semaphore(max_concurrency)
        self._interval = min_interval_seconds
        self._rate_limit_cooldown = rate_limit_cooldown_seconds
        self._schedule_lock = Lock()
        self._next_start_at = 0.0

    def execute(self, operation: Callable[[], T]) -> T:
        with self._semaphore:
            self._wait_for_start_slot()
            try:
                return operation()
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    self._defer_starts(self._rate_limit_cooldown)
                    if self._rate_limit_cooldown > 0:
                        self._wait_for_start_slot()
                        return operation()
                raise

    def _wait_for_start_slot(self) -> None:
        with self._schedule_lock:
            now = monotonic()
            start_at = max(now, self._next_start_at)
            self._next_start_at = start_at + self._interval
        delay = start_at - now
        if delay > 0:
            sleep(delay)

    def _defer_starts(self, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._schedule_lock:
            self._next_start_at = max(self._next_start_at, monotonic() + seconds)


def _is_rate_limit_error(exc: Exception) -> bool:
    status = getattr(exc, "code", None)
    return status == 429 or "429" in str(exc)
