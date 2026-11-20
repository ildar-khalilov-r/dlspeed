"""Shared fakes for the offline test suite."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import httpx
import pytest

from dlspeed import measurement

URL = "https://example.com/big.jpg"


def install(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.BaseTransport, clock: Callable[[], float]
) -> None:
    """Route every attempt through `transport` and time it with `clock`."""
    monkeypatch.setattr(measurement, "_new_transport", lambda: transport)
    monkeypatch.setattr(measurement, "_clock", clock)


def fake_clock(values: Sequence[float]) -> Callable[[], float]:
    """A clock returning `values` in order; an unexpected extra read raises StopIteration."""
    iterator = iter(values)
    return lambda: next(iterator)


def ok_clock(count: int, seconds: float = 0.5) -> Callable[[], float]:
    """A clock for `count` successful attempts that each take `seconds`."""
    values: list[float] = []
    for i in range(count):
        values += [i * 10.0, i * 10.0 + seconds]
    return fake_clock(values)


def serve(body: bytes = b"abc", headers: dict[str, str] | None = None) -> httpx.MockTransport:
    """A transport answering every request with 200 and `body`."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(body), headers=headers)

    return httpx.MockTransport(handler)
