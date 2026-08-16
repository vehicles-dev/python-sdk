from __future__ import annotations

import traceback
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import API_KEY, VIN, json_response

from vehicles_dev import AsyncVehicles, Vehicles, VehiclesError


class ClosingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return json_response({"vin": VIN})

    def close(self) -> None:
        self.closed = True


class AsyncClosingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return json_response({"vin": VIN})

    async def aclose(self) -> None:
        self.closed = True


class BrokenStream(httpx.SyncByteStream):
    def __iter__(self):  # type: ignore[no-untyped-def]
        raise OSError("body read failed")


def test_parses_problem_details_request_id_and_retry_after() -> None:
    problem = {
        "type": "https://vehicles.dev/problems/invalid-request",
        "code": "invalid_request",
        "detail": "Two inputs were invalid.",
        "request_id": "body-request-id",
        "retryable": True,
        "invalid_params": [
            {"name": "state", "pointer": "/query/state", "reason": "must be two letters"},
            {"name": 1, "reason": "ignored malformed entry"},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            problem,
            status=422,
            headers={"Retry-After": "17", "X-Request-Id": "header-request-id"},
        )

    with (
        Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(VehiclesError) as raised,
    ):
        client.decode_vin(VIN)

    error = raised.value
    assert error.status == 422
    assert error.code == "invalid_request"
    assert error.detail == "Two inputs were invalid."
    assert error.type == "https://vehicles.dev/problems/invalid-request"
    assert error.request_id == "body-request-id"
    assert error.retryable is True
    assert error.retry_after_seconds == 17
    assert error.invalid_params == (
        {"name": "state", "pointer": "/query/state", "reason": "must be two letters"},
    )


def test_parses_http_date_retry_after_and_header_request_id() -> None:
    retry_at = datetime.now(UTC) + timedelta(seconds=2)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"code": "busy", "detail": "Try later."},
            status=503,
            headers={
                "Retry-After": retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "X-Request-Id": "req-1",
            },
        )

    with (
        Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(VehiclesError) as raised,
    ):
        client.decode_vin(VIN)

    assert raised.value.request_id == "req-1"
    assert raised.value.retryable is True
    assert raised.value.retry_after_seconds is not None
    assert 0 <= raised.value.retry_after_seconds <= 2


@pytest.mark.parametrize(
    ("status", "body", "code", "retryable"),
    [
        (200, b"not-json", "invalid_response_body", False),
        (503, b"not-json", "unexpected_response", True),
        (400, b"[]", "unexpected_response", False),
    ],
)
def test_maps_invalid_json_and_non_problem_responses(
    status: int,
    body: bytes,
    code: str,
    retryable: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    with (
        Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(VehiclesError) as raised,
    ):
        client.decode_vin(VIN)

    assert raised.value.status == status
    assert raised.value.code == code
    assert raised.value.retryable is retryable


def test_maps_unreadable_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=BrokenStream())

    with (
        Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(VehiclesError) as raised,
    ):
        client.decode_vin(VIN)

    assert raised.value.status == 200
    assert raised.value.code == "response_unreadable"


def test_maps_timeout_and_network_failures_and_redacts_the_api_key() -> None:
    fixture_value = "visible-fixture-value"

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout(f"timeout leaked {fixture_value}", request=request)

    with (
        Vehicles(fixture_value, transport=httpx.MockTransport(timeout_handler)) as client,
        pytest.raises(VehiclesError) as timeout_raised,
    ):
        client.decode_vin(VIN)
    assert timeout_raised.value.code == "request_timeout"
    assert timeout_raised.value.retryable is True

    def network_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"network leaked {fixture_value}", request=request)

    with (
        Vehicles(fixture_value, transport=httpx.MockTransport(network_handler)) as client,
        pytest.raises(VehiclesError) as network_raised,
    ):
        client.decode_vin(VIN)

    error = network_raised.value
    assert error.code == "network_unreachable"
    assert error.status is None
    assert error.retryable is True
    for emitted in (
        str(error),
        repr(error),
        error.detail,
        "".join(traceback.format_exception(error)),
        *error.args,
    ):
        assert fixture_value not in str(emitted)


@pytest.mark.asyncio
async def test_async_maps_transport_and_problem_errors() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("too slow", request=request)
        return json_response({"code": "limited", "detail": "Slow down."}, status=429)

    async with AsyncVehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VehiclesError, match="request_timeout"):
            await client.decode_vin(VIN)
        with pytest.raises(VehiclesError) as raised:
            await client.decode_vin(VIN)

    assert raised.value.code == "limited"
    assert raised.value.retryable is True


def test_sync_client_owns_transport_lifecycle() -> None:
    transport = ClosingTransport()
    client = Vehicles(API_KEY, transport=transport)
    assert client.decode_vin(VIN) == {"vin": VIN}
    client.close()
    assert transport.closed is True


def test_default_timeout_is_thirty_seconds() -> None:
    observed: dict[str, float] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        raw = request.extensions["timeout"]
        assert isinstance(raw, dict)
        observed.update(raw)
        return json_response({})

    with Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        client.decode_vin(VIN)

    assert observed == {"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}


@pytest.mark.asyncio
async def test_async_client_owns_transport_lifecycle() -> None:
    transport = AsyncClosingTransport()
    client = AsyncVehicles(API_KEY, transport=transport)
    assert await client.decode_vin(VIN) == {"vin": VIN}
    await client.aclose()
    assert transport.closed is True
