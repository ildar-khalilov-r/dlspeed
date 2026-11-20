from __future__ import annotations

import errno
from collections.abc import Callable, Sequence

import httpx
import pytest

from dlspeed import Attempt, Failure, Report, Success, cli
from dlspeed.cli import format_report, main
from tests.helpers import URL, fake_clock, install, ok_clock

MB_BODY = b"x" * 1_000_000


def patch_measure(
    monkeypatch: pytest.MonkeyPatch,
    statuses: Sequence[int],
    clock: Callable[[], float],
) -> None:
    status_iter = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(status_iter), stream=httpx.ByteStream(MB_BODY))

    install(monkeypatch, httpx.MockTransport(handler), clock)


def test_format_report_exact_text() -> None:
    report = Report(url=URL, attempts=(Success(1_000_000, 0.5), Success(1_000_000, 0.5)))

    assert format_report(report, 2) == (
        "URL:              https://example.com/big.jpg\n"
        "Requests:         2/2 succeeded\n"
        "Average time:     500.0 ms\n"
        "Total downloaded: 2.00 MB\n"
        "Download speed:   16.00 Mbit/s (2.00 MB/s)"
    )


def test_format_report_zero_elapsed_time() -> None:
    report = Report(url=URL, attempts=(Success(10, 0.0),))

    assert format_report(report, 1).splitlines()[-1] == (
        "Download speed:   n/a (elapsed time too small to measure)"
    )


def test_format_report_all_failed_has_only_url_and_requests() -> None:
    report = Report(url=URL, attempts=(Failure("x"), Failure("y")))

    assert format_report(report, 2) == (
        "URL:              https://example.com/big.jpg\nRequests:         0/2 succeeded"
    )


def test_format_report_counts_requests_that_never_ran() -> None:
    report = Report(url=URL, attempts=(Success(1_000_000, 0.5),))

    assert format_report(report, 5).splitlines()[1] == (
        "Requests:         1/1 succeeded (4 not run)"
    )


def test_main_success_prints_progress_and_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_measure(monkeypatch, [200, 200], ok_clock(2))

    code = main([URL, "--count", "2"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert captured.out == (
        "Request 1/2: 1.00 MB in 500.0 ms\n"
        "Request 2/2: 1.00 MB in 500.0 ms\n"
        "\n"
        "URL:              https://example.com/big.jpg\n"
        "Requests:         2/2 succeeded\n"
        "Average time:     500.0 ms\n"
        "Total downloaded: 2.00 MB\n"
        "Download speed:   16.00 Mbit/s (2.00 MB/s)\n"
    )


def test_main_progress_index_is_right_aligned(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_measure(monkeypatch, [200] * 10, ok_clock(10, seconds=1.0))

    main([URL])

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Request  1/10: 1.00 MB in 1000.0 ms"
    assert lines[9] == "Request 10/10: 1.00 MB in 1000.0 ms"


def test_main_partial_failure_warns_on_stderr_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_measure(monkeypatch, [200, 500], fake_clock([0.0, 0.5, 10.0]))

    code = main([URL, "--count", "2"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Request 2/2: failed (HTTP 500 Internal Server Error)\n" in captured.out
    assert "Requests:         1/2 succeeded\n" in captured.out
    assert captured.err == "Warning: 1 of 2 requests failed.\n"


def test_main_all_failed_exits_one_without_stats(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_measure(monkeypatch, [500, 500], fake_clock([0.0, 1.0]))

    code = main([URL, "--count", "2"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == (
        "Request 1/2: failed (HTTP 500 Internal Server Error)\n"
        "Request 2/2: failed (HTTP 500 Internal Server Error)\n"
        "\n"
        "URL:              https://example.com/big.jpg\n"
        "Requests:         0/2 succeeded\n"
    )
    assert captured.err == "Error: all 2 requests failed.\n"


def test_main_forwards_count_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, float]] = []

    def recorder(
        url: str,
        count: int,
        timeout: float,
        *,
        on_attempt: Callable[[int, Attempt], None] | None,
    ) -> Report:
        calls.append((url, count, timeout))
        return Report(url=url, attempts=(Success(1, 1.0),))

    monkeypatch.setattr(cli, "measure", recorder)

    assert main([URL, "--count", "3", "--timeout", "2.5"]) == 0
    assert main([URL]) == 0
    assert calls == [(URL, 3, 2.5), (URL, 10, 30.0)]


def test_main_ctrl_c_reports_completed_attempts_and_exits_130(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupted_after_one(
        url: str,
        count: int,
        timeout: float,
        *,
        on_attempt: Callable[[int, Attempt], None] | None,
    ) -> Report:
        assert on_attempt is not None
        on_attempt(1, Success(1_000_000, 0.5))
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "measure", interrupted_after_one)

    code = main([URL, "--count", "5"])

    captured = capsys.readouterr()
    assert code == 130
    assert "Requests:         1/1 succeeded (4 not run)\n" in captured.out
    assert "Download speed:   16.00 Mbit/s (2.00 MB/s)\n" in captured.out
    assert captured.err == "Interrupted after 1 of 5 requests.\n"


def test_main_does_not_report_unrelated_value_errors_as_bad_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(
        url: str,
        count: int,
        timeout: float,
        *,
        on_attempt: Callable[[int, Attempt], None] | None,
    ) -> Report:
        raise ValueError("Unknown scheme for proxy URL")

    monkeypatch.setattr(cli, "measure", broken)

    with pytest.raises(ValueError, match="proxy"):
        main([URL])


@pytest.mark.parametrize(
    "error",
    [BrokenPipeError(errno.EPIPE, "Broken pipe"), OSError(errno.EINVAL, "Invalid argument")],
    ids=["posix-epipe", "windows-einval"],
)
def test_main_closed_stdout_pipe_exits_quietly_with_141(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    discarded: list[bool] = []

    def closed_stdout(*args: object, **kwargs: object) -> None:
        raise error

    patch_measure(monkeypatch, [200], ok_clock(1))
    monkeypatch.setattr(cli, "print", closed_stdout, raising=False)
    monkeypatch.setattr(cli, "_discard_stdout", lambda: discarded.append(True))

    assert main([URL, "--count", "1"]) == 141
    assert discarded == [True]


def test_main_other_stdout_errors_still_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    patch_measure(monkeypatch, [200], ok_clock(1))
    monkeypatch.setattr(cli, "print", disk_full, raising=False)

    with pytest.raises(OSError, match="No space"):
        main([URL, "--count", "1"])


def test_main_invalid_url_exits_two_before_any_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["ftp://x"])

    assert excinfo.value.code == 2
    assert "http" in capsys.readouterr().err
