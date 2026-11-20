"""Measure HTTP download speed by fetching a URL several times in a row."""

from dlspeed.measurement import (
    DEFAULT_COUNT,
    DEFAULT_TIMEOUT,
    Attempt,
    Failure,
    InvalidArgumentError,
    Report,
    Success,
    measure,
)

__all__ = [
    "DEFAULT_COUNT",
    "DEFAULT_TIMEOUT",
    "Attempt",
    "Failure",
    "InvalidArgumentError",
    "Report",
    "Success",
    "measure",
]
