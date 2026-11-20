from __future__ import annotations

import gzip
import math
from importlib.metadata import version

import httpx
import pytest

from dlspeed import (
    Attempt,
    Failure,
    InvalidArgumentError,
    Report,
    Success,
    measure,
    measurement,
)
from tests.helpers import URL, fake_clock, install, ok_clock, serve


def test_ten_successes_exact_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b"x" * 1000
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        events.append("request")
        return httpx.Response(200, stream=httpx.ByteStream(body))

    def on_attempt(index: int, attempt: Attempt) -> None:
        events.append(f"attempt {index}")

    install(monkeypatch, httpx.MockTransport(handler), ok_clock(10))
    report = measure(URL, on_attempt=on_attempt)

    assert len(report.successes) == 10
    assert report.failures == ()
    assert report.total_bytes == 10_000
    assert report.total_seconds == 5.0
    assert report.average_seconds == 0.5
    assert report.bytes_per_second == 2000.0
    expected = [e for i in range(1, 11) for e in ("request", f"attempt {i}")]
    assert events == expected


def test_accept_encoding_identity_sent_on_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Accept-Encoding"))
        return httpx.Response(200, stream=httpx.ByteStream(b"abc"))

    install(monkeypatch, httpx.MockTransport(handler), ok_clock(3))
    measure(URL, count=3)

    assert seen == ["identity"] * 3


def test_every_attempt_uses_a_fresh_client_and_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[httpx.Client] = []
    real_client = httpx.Client

    class CountingClient(real_client):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)  # type: ignore[arg-type]  # kwargs forwarded verbatim
            created.append(self)

    monkeypatch.setattr(httpx, "Client", CountingClient)
    transport = serve()
    factory_calls: list[httpx.BaseTransport] = []

    def counting_factory() -> httpx.BaseTransport:
        factory_calls.append(transport)
        return transport

    monkeypatch.setattr(measurement, "_new_transport", counting_factory)
    monkeypatch.setattr(measurement, "_clock", ok_clock(4))

    measure(URL, count=4)

    assert len(created) == 4
    assert len({id(client) for client in created}) == 4
    assert len(factory_calls) == 4


def test_default_transport_lets_each_client_build_its_own_pool() -> None:
    # pyright: ignore below: reading the private seam is the point of this test.
    assert measurement._new_transport() is None  # pyright: ignore[reportPrivateUsage]


def test_user_agent_names_the_tool_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("User-Agent"))
        return httpx.Response(200, stream=httpx.ByteStream(b"abc"))

    install(monkeypatch, httpx.MockTransport(handler), ok_clock(1))
    measure(URL, count=1)

    assert seen == [f"dlspeed/{version('dlspeed')}"]


def test_wire_bytes_are_counted_not_decoded_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    compressed = gzip.compress(b"a" * 10_000)
    install(monkeypatch, serve(compressed, headers={"Content-Encoding": "gzip"}), ok_clock(2))

    report = measure(URL, count=2)

    assert report.total_bytes == 2 * len(compressed)
    assert len(compressed) < 10_000


def test_redirect_is_followed_and_final_body_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/big.jpg":
            return httpx.Response(302, headers={"Location": "/real.jpg"})
        return httpx.Response(200, stream=httpx.ByteStream(b"y" * 500))

    install(monkeypatch, httpx.MockTransport(handler), ok_clock(2))
    report = measure(URL, count=2)

    assert len(report.successes) == 2
    assert report.total_bytes == 1000


def test_http_errors_are_failures_and_stats_cover_successes_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = iter([200, 500, 404, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), stream=httpx.ByteStream(b"z" * 100))

    install(
        monkeypatch,
        httpx.MockTransport(handler),
        fake_clock([0.0, 1.0, 10.0, 20.0, 30.0, 32.0]),
    )
    report = measure(URL, count=4)

    assert report.failures == (
        Failure("HTTP 500 Internal Server Error"),
        Failure("HTTP 404 Not Found"),
    )
    assert report.successes == (Success(100, 1.0), Success(100, 2.0))
    assert report.total_bytes == 200
    assert report.average_seconds == 1.5
    assert report.bytes_per_second == 200 / 3.0


def test_network_errors_are_failures_with_type_name(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = iter([httpx.ConnectError("boom"), httpx.ReadTimeout("")])

    def handler(request: httpx.Request) -> httpx.Response:
        raise next(errors)

    install(monkeypatch, httpx.MockTransport(handler), fake_clock([0.0, 1.0]))
    report = measure(URL, count=2)

    assert report.failures == (Failure("ConnectError: boom"), Failure("ReadTimeout"))


def test_all_attempts_fail_gives_no_averages(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    install(monkeypatch, httpx.MockTransport(handler), fake_clock([0.0, 1.0, 2.0]))
    report = measure(URL, count=3)

    assert report.successes == ()
    assert report.total_bytes == 0
    assert report.average_seconds is None
    assert report.bytes_per_second is None


def test_zero_byte_body_gives_zero_speed(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, serve(b""), ok_clock(1))

    report = measure(URL, count=1)

    assert report.total_bytes == 0
    assert report.bytes_per_second == 0.0


def test_zero_elapsed_time_gives_no_speed(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, serve(), fake_clock([5.0, 5.0]))

    report = measure(URL, count=1)

    assert report.average_seconds == 0.0
    assert report.bytes_per_second is None


def test_on_attempt_receives_indexes_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses = iter([200, 500, 200])
    received: list[tuple[int, Attempt]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), stream=httpx.ByteStream(b"ab"))

    install(monkeypatch, httpx.MockTransport(handler), fake_clock([0.0, 1.0, 5.0, 10.0, 11.0]))
    report = measure(
        URL,
        count=3,
        on_attempt=lambda index, attempt: received.append((index, attempt)),
    )

    assert [index for index, _ in received] == [1, 2, 3]
    assert tuple(attempt for _, attempt in received) == report.attempts


@pytest.mark.parametrize(
    ("url", "count", "timeout"),
    [
        (URL, 0, 30.0),
        (URL, 10, 0.0),
        (URL, 10, -1.0),
        (URL, 10, math.inf),
        (URL, 10, math.nan),
        ("http://xn--a/", 10, 30.0),
        ("ftp://x", 10, 30.0),
        ("not a url", 10, 30.0),
        ("http://", 10, 30.0),
    ],
)
def test_invalid_arguments_raise_before_any_request(
    monkeypatch: pytest.MonkeyPatch, url: str, count: int, timeout: float
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be made")

    install(monkeypatch, httpx.MockTransport(handler), fake_clock([]))

    with pytest.raises(InvalidArgumentError):
        measure(url, count, timeout)


def test_report_without_attempts_is_empty() -> None:
    report = Report(url=URL, attempts=())

    assert report.total_bytes == 0
    assert report.average_seconds is None
    assert report.bytes_per_second is None
