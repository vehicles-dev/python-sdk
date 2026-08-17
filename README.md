# vehicles-dev

Official, fully typed Python SDK for the [Vehicles.dev](https://vehicles.dev) API. It supports
Python 3.11 and newer, with synchronous and asynchronous clients powered by `httpx`.

> [!WARNING]
> This SDK is server-side only. Never put a Vehicles.dev API key in browser code, a public bundle,
> a mobile application, or any client-visible environment variable. The API deliberately rejects
> browser `Origin` and `Cookie` headers.

## Install

The PyPI release is not enabled yet. Install the tagged starter directly from GitHub:

```sh
pip install "vehicles-dev @ git+https://github.com/vehicles-dev/python-sdk.git@v0.1.1"
```

Once the PyPI package is published, the install command will be `pip install vehicles-dev`.

## Quick start

```python
import os

from vehicles_dev import Vehicles

with Vehicles(os.environ["VEHICLES_API_KEY"]) as vehicles:
    decoded = vehicles.decode_vin("1HGCM82633A004352")
    vehicle = decoded["vehicle"]
    print({field: vehicle.get(field) for field in ("make", "model", "year")})

    value = vehicles.get_market_value(
        year=2003,
        make="Honda",
        model="Accord",
        miles=82_000,
        state="TX",
    )
    print(value["estimateUsd"])
```

Use `AsyncVehicles` in async applications:

```python
import os

from vehicles_dev import AsyncVehicles

async with AsyncVehicles(os.environ["VEHICLES_API_KEY"]) as vehicles:
    decoded = await vehicles.decode_vin("1HGCM82633A004352")
```

Both constructors reject a missing or blank key before making a request. By default they call
`https://api.vehicles.dev` with a 30-second timeout. A test, proxy, or self-hosted gateway can be
configured explicitly:

```python
vehicles = Vehicles(
    os.environ["VEHICLES_API_KEY"],
    base_url="https://gateway.example.com/vehicles",
    timeout=20,
    transport=custom_httpx_transport,
)
```

Custom remote base URLs must use HTTPS. Plain HTTP is accepted only for explicit local development
on `localhost`, IPv4 loopback (`127.0.0.0/8`), or IPv6 loopback (`::1`).

The SDK owns the injected transport's lifecycle. Close the client, or use its context manager. The
client sends `Authorization`, `Accept`, and `User-Agent`; it sends `Content-Type` only with a JSON
POST body. It never adds `Origin` or `Cookie`, never follows redirects, and never automatically
retries a metered operation.

## Operations

The same methods are available on `Vehicles` and `AsyncVehicles`; async methods are awaited.
Arguments and options use idiomatic `snake_case` and map to the API wire names.

| Method | API operation |
| --- | --- |
| `decode_vin(vin)` | `GET /v1/vehicles/vin/{vin}` |
| `get_specifications(vin)` | `GET /v1/vehicles/specifications/{vin}` |
| `get_recalls(vin)` | `GET /v1/vehicles/recalls/{vin}` |
| `get_photos(vin)` | `GET /v1/vehicles/photos/{vin}` |
| `search_listings(...)` | `GET /v1/vehicles/listings` |
| `get_market_value(...)` | `GET /v1/vehicles/market-value` |
| `get_depreciation(...)` | `GET /v1/vehicles/depreciation` |
| `get_ownership_costs(...)` | `GET /v1/vehicles/ownership-costs` |
| `history_reports.create(vin, ...)` | `POST /v1/vehicles/history-reports` |
| `history_reports.retry_submission(id)` | `POST /v1/vehicles/history-reports/{id}/retry` |
| `history_reports.get_status(id)` | `GET /v1/vehicles/history-reports/{id}` |
| `history_reports.get_result(id)` | `GET /v1/vehicles/history-reports/{id}/result` |

VIN path values are trimmed, uppercased, validated, and percent-encoded. Immediate data endpoints
require exactly 17 VIN-safe characters matching `[A-HJ-NPR-Za-hj-npr-z0-9]{17}`; the letters I, O,
and Q are not allowed. Durable history-report creation uppercases its body value but leaves the
route's strict 17-character VIN validation to the server.

### Listings

```python
listings = vehicles.search_listings(
    active=True,
    make="Ford",
    mileage_max=80_000,
    price_max=35_000,
    sold=False,
    sort="days_on_market",
    year_min=2020,
)
```

### Depreciation and ownership costs

```python
depreciation = vehicles.get_depreciation(make="Toyota", model="Camry")
ownership = vehicles.get_ownership_costs(year=2024, make="Toyota", model="Camry")
```

## Durable history reports

History reports are asynchronous at the API level and separately metered. Generate one UUID for
one logical order, persist it before the request, and reuse it if the create response is lost. The
SDK requires your UUID and never replaces or generates it.

```python
from uuid import uuid4

idempotency_key = str(uuid4())  # Persist this with the logical order before sending.
created = vehicles.history_reports.create(
    "1HGCM82633A004352",
    idempotency_key=idempotency_key,
)
result = vehicles.history_reports.wait_for_result(created["id"], max_wait=300)
print(sorted(result["report"]))  # Section names only; do not log the full report.
```

`wait_for_result` polls read-only status at each response's `retryAfterSeconds` cadence and fetches
the result only after `completed` plus `hasResult: true`. A positive `poll_interval` override is
available for deterministic tests. The waiter stops with `VehiclesError` on `action_required`, an
invalid state, timeout, or an `abort_event`. Async task cancellation remains normal
`asyncio.CancelledError` cancellation and its sleeps never block the event loop. The original
`max_wait` deadline also caps each in-flight status and result request.

The waiter never calls `retry_submission`, because resubmission is explicit and can have billing
consequences. If result retrieval returns `409 report_not_ready`, it honors `Retry-After` and resumes
status polling inside the original deadline. Explicit resubmission is available only when required:

```python
created = vehicles.history_reports.retry_submission(created["id"])
```

## Errors

API, response, transport, and waiter failures raise `VehiclesError`:

```python
from vehicles_dev import VehiclesError

try:
    vehicles.get_photos("1HGCM82633A004352")
except VehiclesError as error:
    print(
        {
            "status": error.status,
            "code": error.code,
            "request_id": error.request_id,
            "retryable": error.retryable,
        }
    )
```

Vehicle history reports and decoded payloads can contain sensitive vehicle or owner-adjacent data.
Do not raw-log report payloads, decoded records, `VehiclesError` objects, or their tracebacks. Log
only explicitly allowlisted operational fields such as `status`, `code`, and `request_id`.

Errors expose `status`, `code`, `detail`, `type`, `request_id`, `retryable`, `invalid_params`, and
`retry_after_seconds`. HTTP failures parse RFC 9457 problem documents. Timeouts, network failures,
unreadable bodies, and invalid JSON use stable local codes. The configured API key is redacted from
every error string and representation.

Response `TypedDict` definitions preserve the API's JSON field names, while open provider payloads
remain `dict[str, Any]` and dates remain strings. A `py.typed` marker is included in the wheel.

## Development

```sh
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv run python -m build
uv run twine check dist/*
```

Tests use injected transports and never contact production or make billable requests.

## License

MIT. API access and returned data remain subject to the
[Vehicles.dev terms of service](https://vehicles.dev/terms).
