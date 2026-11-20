"""Sequential HTTP download measurement."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import TypeAlias

import httpx


def _user_agent() -> str:
    try:
        return f"dlspeed/{version('dlspeed')}"
    except PackageNotFoundError:
        return "dlspeed"


# IMPORTANT: some hosts (Wikimedia among them) answer 403 to a generic library User-Agent.
USER_AGENT = _user_agent()
DEFAULT_COUNT = 10
DEFAULT_TIMEOUT = 30.0


class InvalidArgumentError(ValueError):
    """An argument to `measure` was rejected before any request was made."""


@dataclass(frozen=True)
class Success:
    """A request whose body was fully downloaded."""

    num_bytes: int
    seconds: float


@dataclass(frozen=True)
class Failure:
    """A request that did not complete, with a human-readable reason."""

    reason: str


Attempt: TypeAlias = Success | Failure


@dataclass(frozen=True)
class Report:
    """The outcome of all attempts against one URL, with statistics derived from them."""

    url: str
    attempts: tuple[Attempt, ...]

    @property
    def successes(self) -> tuple[Success, ...]:
        """The attempts that completed."""
        return tuple(a for a in self.attempts if isinstance(a, Success))

    @property
    def failures(self) -> tuple[Failure, ...]:
        """The attempts that did not complete."""
        return tuple(a for a in self.attempts if isinstance(a, Failure))

    @property
    def total_bytes(self) -> int:
        """Bytes downloaded across successful attempts."""
        return sum(s.num_bytes for s in self.successes)

    @property
    def total_seconds(self) -> float:
        """Time spent across successful attempts."""
        return sum(s.seconds for s in self.successes)

    @property
    def average_seconds(self) -> float | None:
        """Mean time of a successful attempt, or None when there were none."""
        successes = self.successes
        if not successes:
            return None
        return self.total_seconds / len(successes)

    @property
    def bytes_per_second(self) -> float | None:
        """Total bytes over total time, or None when it cannot be computed."""
        if not self.successes or self.total_seconds == 0:
            return None
        return self.total_bytes / self.total_seconds


def _validate(url: str, count: int, timeout: float) -> None:
    if count < 1:
        raise InvalidArgumentError(f"count must be at least 1, got {count}")
    if not math.isfinite(timeout) or timeout <= 0:
        raise InvalidArgumentError(f"timeout must be a positive number, got {timeout}")
    try:
        parsed = httpx.URL(url)
        host = parsed.host
    except (httpx.InvalidURL, UnicodeError) as exc:
        raise InvalidArgumentError(f"invalid URL {url!r}: {exc}") from exc
    if parsed.scheme not in ("http", "https") or not host:
        raise InvalidArgumentError(f"URL must be http or https with a host, got {url!r}")


def _new_transport() -> httpx.BaseTransport | None:
    """The transport for one attempt; None lets httpx open a fresh connection pool."""
    return None


_clock: Callable[[], float] = time.perf_counter


def _attempt(url: str, timeout: float) -> Attempt:
    try:
        with httpx.Client(
            transport=_new_transport(),
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept-Encoding": "identity", "User-Agent": USER_AGENT},
        ) as client:
            start = _clock()
            with client.stream("GET", url) as response:
                response.raise_for_status()
                num_bytes = sum(len(chunk) for chunk in response.iter_raw())
            end = _clock()
    except httpx.HTTPStatusError as exc:
        return Failure(f"HTTP {exc.response.status_code} {exc.response.reason_phrase}".rstrip())
    except httpx.HTTPError as exc:
        return Failure(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
    return Success(num_bytes, end - start)


def measure(
    url: str,
    count: int = DEFAULT_COUNT,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    on_attempt: Callable[[int, Attempt], None] | None = None,
) -> Report:
    """Fetch `url` `count` times in a row, each on a fresh connection, and report the results."""
    _validate(url, count, timeout)
    attempts: list[Attempt] = []
    for index in range(1, count + 1):
        attempt = _attempt(url, timeout)
        attempts.append(attempt)
        if on_attempt is not None:
            on_attempt(index, attempt)
    return Report(url=url, attempts=tuple(attempts))
