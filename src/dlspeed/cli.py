"""Command-line interface for dlspeed."""

from __future__ import annotations

import argparse
import errno
import os
import sys
from collections.abc import Sequence

from dlspeed.measurement import (
    DEFAULT_COUNT,
    DEFAULT_TIMEOUT,
    Attempt,
    Failure,
    InvalidArgumentError,
    Report,
    measure,
)

BYTES_PER_MB = 10**6
BITS_PER_MBIT = 10**6
BITS_PER_BYTE = 8
MS_PER_SECOND = 1000
EXIT_INTERRUPTED = 130
EXIT_BROKEN_PIPE = 141


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dlspeed",
        description=(
            "Download a URL several times in a row and report the average request time (ms), "
            "total data (MB, 10^6 bytes) and download speed (Mbit/s and MB/s)."
        ),
    )
    parser.add_argument("url", help="http(s) URL to download")
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_COUNT,
        help="number of requests (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="network timeout in seconds, applied to the connect and to each read "
        "(default: %(default)s)",
    )
    return parser


def format_report(report: Report, planned: int) -> str:
    """Render a report of `planned` requests as the summary printed after the progress lines."""
    requests = f"Requests:         {len(report.successes)}/{len(report.attempts)} succeeded"
    not_run = planned - len(report.attempts)
    if not_run > 0:
        requests += f" ({not_run} not run)"
    lines = [f"URL:              {report.url}", requests]
    average = report.average_seconds
    if average is None:
        return "\n".join(lines)
    lines.append(f"Average time:     {average * MS_PER_SECOND:.1f} ms")
    lines.append(f"Total downloaded: {report.total_bytes / BYTES_PER_MB:.2f} MB")
    speed = report.bytes_per_second
    if speed is None:
        lines.append("Download speed:   n/a (elapsed time too small to measure)")
    else:
        mbit = speed * BITS_PER_BYTE / BITS_PER_MBIT
        lines.append(f"Download speed:   {mbit:.2f} Mbit/s ({speed / BYTES_PER_MB:.2f} MB/s)")
    return "\n".join(lines)


def _format_progress(index: int, count: int, attempt: Attempt) -> str:
    prefix = f"Request {index:>{len(str(count))}}/{count}:"
    if isinstance(attempt, Failure):
        return f"{prefix} failed ({attempt.reason})"
    return (
        f"{prefix} {attempt.num_bytes / BYTES_PER_MB:.2f} MB "
        f"in {attempt.seconds * MS_PER_SECOND:.1f} ms"
    )


class _ReaderGone(Exception):
    """Whatever was reading stdout closed it, e.g. `dlspeed URL | head -1`."""


def _out(text: str = "") -> None:
    # IMPORTANT: flushed per line so progress shows live and the summary precedes any stderr
    # warning when stdout is piped.
    try:
        print(text, flush=True)
    except OSError as exc:
        # IMPORTANT: Windows reports a write to a closed pipe as EINVAL, not EPIPE.
        if isinstance(exc, BrokenPipeError) or exc.errno == errno.EINVAL:
            raise _ReaderGone from exc
        raise


def _print_summary(report: Report, planned: int) -> None:
    _out()
    _out(format_report(report, planned))


def _discard_stdout() -> None:
    # IMPORTANT: the interpreter flushes stdout again at exit; pointing the descriptor at devnull
    # keeps that flush from failing a second time after the reader has gone.
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stdout.fileno())


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line tool and return its exit code."""
    try:
        return _run(argv)
    except _ReaderGone:
        _discard_stdout()
        return EXIT_BROKEN_PIPE


def _run(argv: Sequence[str] | None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    count: int = args.count
    completed: list[Attempt] = []

    def show_progress(index: int, attempt: Attempt) -> None:
        completed.append(attempt)
        _out(_format_progress(index, count, attempt))

    try:
        report = measure(args.url, count, args.timeout, on_attempt=show_progress)
    except InvalidArgumentError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        _print_summary(Report(url=args.url, attempts=tuple(completed)), count)
        print(f"Interrupted after {len(completed)} of {count} requests.", file=sys.stderr)
        return EXIT_INTERRUPTED

    _print_summary(report, count)
    failed = len(report.failures)
    if not report.successes:
        print(f"Error: all {count} requests failed.", file=sys.stderr)
        return 1
    if failed:
        print(f"Warning: {failed} of {count} requests failed.", file=sys.stderr)
    return 0
