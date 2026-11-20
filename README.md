# dlspeed

Measure HTTP download speed by fetching a URL several times in a row.

`dlspeed` makes 10 sequential GET requests to a URL, reads each response body to the end, and
prints the average request time, the total amount of data downloaded, and the download speed. It
is a console script and an importable module.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer.

```sh
uv sync                                               # install
uv run dlspeed https://proof.ovh.net/files/10Mb.dat   # run
uv run pytest                                         # test (offline)
```

Without uv: `pip install .`, then `dlspeed <url>` or `python -m dlspeed <url>`.

## Usage

```
dlspeed [-h] [--count COUNT] [--timeout TIMEOUT] url
```

| Argument | Meaning |
| --- | --- |
| `url` | The http or https URL to download. |
| `--count` | Number of sequential requests. Default: 10. |
| `--timeout` | Per-request network timeout in seconds (connect, and each read). Default: 30. |

Put the URL in quotes when it has a query string. `&` and `?` mean something to the shell
(PowerShell rejects an unquoted `&` outright), so the shell would split or rewrite the URL before
`dlspeed` sees it:

```sh
uv run dlspeed "https://example.com/image.jpg?w=1280&h=720"
```

Sample output (a real run; request 4 hit a read timeout):

```
Request  1/10: 10.49 MB in 5572.8 ms
Request  2/10: 10.49 MB in 4557.6 ms
Request  3/10: 10.49 MB in 5505.7 ms
Request  4/10: failed (ReadTimeout: The read operation timed out)
Request  5/10: 10.49 MB in 4735.7 ms
Request  6/10: 10.49 MB in 5253.6 ms
Request  7/10: 10.49 MB in 5570.7 ms
Request  8/10: 10.49 MB in 2093.4 ms
Request  9/10: 10.49 MB in 2416.7 ms
Request 10/10: 10.49 MB in 4414.5 ms

URL:              https://proof.ovh.net/files/10Mb.dat
Requests:         9/10 succeeded
Average time:     4457.9 ms
Total downloaded: 94.37 MB
Download speed:   18.82 Mbit/s (2.35 MB/s)
Warning: 1 of 10 requests failed.
```

## Units

All units are **decimal (SI)**:

| Quantity | Unit | Definition |
| --- | --- | --- |
| Request time | ms | milliseconds |
| Downloaded data | MB | megabytes, 1 MB = 1,000,000 bytes |
| Speed | Mbit/s | megabits per second, 1 Mbit = 1,000,000 bits. This is how ISPs and speed tests quote bandwidth. |
| Speed | MB/s | megabytes per second, shown alongside for comparison with file sizes |

A file of 10 MiB (10,485,760 bytes) therefore shows as 10.49 MB.

## What is measured

- **Request time** runs from sending the request until the last byte of the body is read. It
  includes DNS lookup, TCP and TLS setup, and any redirects. **Average time** is the mean over
  successful requests.
- **Each request opens a fresh connection**, so every sample pays the same setup cost and none of
  them reuses an earlier handshake.
- **Downloaded data** counts the bytes of the response body as transferred, before any
  decompression. Headers, TLS overhead and the bodies of redirect responses aren't counted. The
  request sends `Accept-Encoding: identity`, which asks the server not to compress the body. If a
  server compresses it anyway, the compressed bytes are counted, because those are what was
  transferred. The body is streamed and discarded, so a large file isn't held in memory.
- **Download speed** is the total bytes divided by the total time of the successful requests. That
  is the aggregate throughput. An average of per-request speeds would over-weight fast requests.

## Failures

A failed request (a timeout, a connection error, or an HTTP 4xx/5xx status) is reported with its
reason, and the run continues. The statistics cover only successful requests; a failed request
contributes neither bytes nor time.

| Exit code | Meaning |
| --- | --- |
| 0 | At least one request succeeded. If some failed, a warning goes to stderr. |
| 1 | Every request failed. |
| 2 | Invalid arguments, for example a URL that isn't http or https. |
| 130 | Interrupted with Ctrl-C. The summary covers the requests that had finished. |
| 141 | Whatever was reading stdout closed it (for example `dlspeed URL \| head -1`). The run stops quietly. |

Some hosts block or rate-limit automated clients. `dlspeed` sends `User-Agent: dlspeed/<version>`;
Wikimedia, for example, may still answer `429 Too Many Requests` to repeated runs.

## Using it as a module

```python
from dlspeed import Failure, measure

report = measure("https://proof.ovh.net/files/10Mb.dat", count=5, timeout=10.0)

print(report.total_bytes, report.average_seconds, report.bytes_per_second)
for attempt in report.failures:
    print(attempt.reason)
```

`total_bytes` is an `int`. `average_seconds` and `bytes_per_second` are `float | None`, and are
`None` when no request succeeded.

`measure()` never prints. It returns a `Report` whose `attempts` are `Success(num_bytes, seconds)`
or `Failure(reason)` values; every statistic is derived from them. It raises
`InvalidArgumentError` (a `ValueError`) before any request for an invalid URL, `count < 1`, or a
timeout that isn't a positive finite number. An optional keyword `on_attempt(index, attempt)`
callback reports progress as each request finishes.

## Development

```sh
uv run pytest                 # tests, offline via httpx.MockTransport
uv run ruff check             # lint
uv run ruff format --check    # formatting
uv run pyright                # strict type check
```
