from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import httpx
import pytest
from conftest import API_KEY, IDEMPOTENCY_KEY, REPORT_ID, VIN, json_response

import vehicles_dev.client as client_module
from vehicles_dev import AsyncVehicles, Vehicles, VehiclesError


def report_view(
    status: str,
    *,
    has_result: bool = False,
    retry_after_seconds: int = 7,
) -> dict[str, Any]:
    return {
        "createdAt": "2026-08-17T00:00:00Z",
        "hasResult": has_result,
        "id": REPORT_ID,
        "retryAfterSeconds": retry_after_seconds,
        "status": status,
        "updatedAt": "2026-08-17T00:00:01Z",
        "vin": VIN,
    }


def install_sync_clock(monkeypatch: pytest.MonkeyPatch) -> tuple[list[float], list[float]]:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(client_module, "_monotonic", lambda: now[0])
    monkeypatch.setattr(client_module, "_sleep", sleep)
    return now, sleeps


def install_async_clock(monkeypatch: pytest.MonkeyPatch) -> tuple[list[float], list[float]]:
    now = [0.0]
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(client_module, "_monotonic", lambda: now[0])
    monkeypatch.setattr(client_module, "_async_sleep", sleep)
    return now, sleeps


def request_timeout(request: httpx.Request) -> float:
    raw = request.extensions["timeout"]
    assert isinstance(raw, dict)
    values = {float(value) for value in raw.values() if value is not None}
    assert len(values) == 1
    return values.pop()


def test_create_requires_and_preserves_caller_uuid_without_generation() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response({**report_view("queued"), "replayed": False}, status=202)

    with Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        for invalid in ("", "not-a-uuid", "00000000-0000-0000-0000-000000000000"):
            with pytest.raises(ValueError, match="caller-supplied stable UUID"):
                client.history_reports.create(VIN, idempotency_key=invalid)
        result = client.history_reports.create(
            " 1hgcm82633a004352 ", idempotency_key=IDEMPOTENCY_KEY
        )

    assert result["replayed"] is False
    assert len(requests) == 1
    assert requests[0].headers["idempotency-key"] == IDEMPOTENCY_KEY
    assert json.loads(requests[0].content) == {"vin": VIN}


def test_wait_for_result_polls_server_cadence_and_never_retries_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sleeps = install_sync_clock(monkeypatch)
    statuses = iter(
        [report_view("queued", retry_after_seconds=9), report_view("completed", has_result=True)]
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/result"):
            return json_response({"report": {"ownerCount": 2}})
        return json_response(next(statuses))

    with Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        result = client.history_reports.wait_for_result(REPORT_ID, max_wait=30)

    assert result == {"report": {"ownerCount": 2}}
    assert sleeps == [9]
    assert [request.method for request in requests] == ["GET", "GET", "GET"]
    assert all(not request.url.path.endswith("/retry") for request in requests)


def test_report_not_ready_honors_retry_after_then_resumes_status_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sleeps = install_sync_clock(monkeypatch)
    statuses = iter(
        [
            report_view("completed", has_result=True),
            report_view("processing", retry_after_seconds=3),
            report_view("completed", has_result=True),
        ]
    )
    result_calls = 0
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal result_calls
        paths.append(request.url.path)
        if request.url.path.endswith("/result"):
            result_calls += 1
            if result_calls == 1:
                return json_response(
                    {"code": "report_not_ready", "detail": "Still preparing."},
                    status=409,
                    headers={"Retry-After": "4"},
                )
            return json_response({"report": {"ready": True}})
        return json_response(next(statuses))

    with Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        result = client.history_reports.wait_for_result(REPORT_ID, max_wait=20)

    assert result == {"report": {"ready": True}}
    assert sleeps == [4, 3]
    assert paths == [
        f"/v1/vehicles/history-reports/{REPORT_ID}",
        f"/v1/vehicles/history-reports/{REPORT_ID}/result",
        f"/v1/vehicles/history-reports/{REPORT_ID}",
        f"/v1/vehicles/history-reports/{REPORT_ID}",
        f"/v1/vehicles/history-reports/{REPORT_ID}/result",
    ]


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (report_view("action_required"), "report_action_required"),
        (report_view("completed", has_result=False), "invalid_report_state"),
        (report_view("surprising"), "invalid_report_state"),
    ],
)
def test_wait_surfaces_terminal_and_invalid_states(payload: dict[str, Any], code: str) -> None:
    with (
        Vehicles(
            API_KEY, transport=httpx.MockTransport(lambda request: json_response(payload))
        ) as client,
        pytest.raises(VehiclesError) as raised,
    ):
        client.history_reports.wait_for_result(REPORT_ID, max_wait=10, poll_interval=1)

    assert raised.value.code == code


def test_wait_enforces_positive_options_deadline_and_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    _, sleeps = install_sync_clock(monkeypatch)
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return json_response(report_view("queued", retry_after_seconds=20))

    with Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        for invalid in (0, -1):
            with pytest.raises(ValueError, match="positive"):
                client.history_reports.wait_for_result(REPORT_ID, poll_interval=invalid)
        with pytest.raises(VehiclesError) as timed_out:
            client.history_reports.wait_for_result(REPORT_ID, max_wait=3, poll_interval=2)
        aborted = threading.Event()
        aborted.set()
        with pytest.raises(VehiclesError) as abort_error:
            client.history_reports.wait_for_result(REPORT_ID, abort_event=aborted)

    assert timed_out.value.code == "report_wait_timeout"
    assert timed_out.value.retryable is True
    assert sleeps == [2, 1]
    assert abort_error.value.code == "request_aborted"
    assert requests == 2


def test_sync_wait_caps_stalled_status_request_to_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now, _ = install_sync_clock(monkeypatch)
    observed_timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeout = request_timeout(request)
        observed_timeouts.append(timeout)
        now[0] += timeout
        raise httpx.ReadTimeout("stalled status", request=request)

    with (
        Vehicles(API_KEY, timeout=30, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(VehiclesError) as raised,
    ):
        client.history_reports.wait_for_result(REPORT_ID, max_wait=5)

    assert raised.value.code == "report_wait_timeout"
    assert raised.value.retryable is True
    assert observed_timeouts == [5]


def test_sync_wait_caps_stalled_result_request_to_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now, _ = install_sync_clock(monkeypatch)
    observed_timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeout = request_timeout(request)
        observed_timeouts.append(timeout)
        if request.url.path.endswith("/result"):
            now[0] += timeout
            raise httpx.ReadTimeout("stalled result", request=request)
        return json_response(report_view("completed", has_result=True))

    with (
        Vehicles(API_KEY, timeout=30, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(VehiclesError) as raised,
    ):
        client.history_reports.wait_for_result(REPORT_ID, max_wait=4)

    assert raised.value.code == "report_wait_timeout"
    assert observed_timeouts == [4, 4]


def test_sync_wait_translates_late_api_error_to_deadline_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now, _ = install_sync_clock(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        now[0] += request_timeout(request)
        return json_response({"code": "busy", "detail": "Try later."}, status=503)

    with (
        Vehicles(API_KEY, timeout=30, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(VehiclesError) as raised,
    ):
        client.history_reports.wait_for_result(REPORT_ID, max_wait=3)

    assert raised.value.code == "report_wait_timeout"


@pytest.mark.asyncio
async def test_async_wait_has_equivalent_polling_and_not_ready_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sleeps = install_async_clock(monkeypatch)
    statuses = iter(
        [
            report_view("queued", retry_after_seconds=6),
            report_view("completed", has_result=True),
            report_view("processing", retry_after_seconds=8),
            report_view("completed", has_result=True),
        ]
    )
    result_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal result_calls
        if request.url.path.endswith("/result"):
            result_calls += 1
            if result_calls == 1:
                return json_response(
                    {"code": "report_not_ready", "detail": "Still preparing."},
                    status=409,
                    headers={"Retry-After": "5"},
                )
            return json_response({"report": {"ready": True}})
        return json_response(next(statuses))

    async with AsyncVehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        result = await client.history_reports.wait_for_result(REPORT_ID, max_wait=30)

    assert result == {"report": {"ready": True}}
    assert sleeps == [6, 5, 8]


@pytest.mark.asyncio
async def test_async_wait_supports_abort_and_task_cancellation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(report_view("queued", retry_after_seconds=60))

    async with AsyncVehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        abort_event = asyncio.Event()
        abort_event.set()
        with pytest.raises(VehiclesError) as aborted:
            await client.history_reports.wait_for_result(REPORT_ID, abort_event=abort_event)
        assert aborted.value.code == "request_aborted"

        task = asyncio.create_task(
            client.history_reports.wait_for_result(REPORT_ID, max_wait=120, poll_interval=60)
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_async_wait_surfaces_action_required_and_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sleeps = install_async_clock(monkeypatch)
    payload = report_view("action_required")

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(payload)

    async with AsyncVehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VehiclesError) as action_required:
            await client.history_reports.wait_for_result(REPORT_ID)
        assert action_required.value.code == "report_action_required"

        payload = report_view("processing", retry_after_seconds=20)
        with pytest.raises(VehiclesError) as timed_out:
            await client.history_reports.wait_for_result(
                REPORT_ID,
                max_wait=3,
                poll_interval=2,
            )

    assert timed_out.value.code == "report_wait_timeout"
    assert sleeps == [2, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [report_view("completed"), report_view("unknown")])
async def test_async_wait_surfaces_completed_without_result_and_unknown_states(
    payload: dict[str, Any],
) -> None:
    async with AsyncVehicles(
        API_KEY, transport=httpx.MockTransport(lambda request: json_response(payload))
    ) as client:
        with pytest.raises(VehiclesError) as raised:
            await client.history_reports.wait_for_result(REPORT_ID)

    assert raised.value.code == "invalid_report_state"


class StalledAsyncReportTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, stall_result: bool) -> None:
        self.stall_result = stall_result
        self.observed_timeouts: list[float] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.observed_timeouts.append(request_timeout(request))
        if not self.stall_result or request.url.path.endswith("/result"):
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        return json_response(report_view("completed", has_result=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("stall_result", [False, True])
async def test_async_wait_enforces_deadline_during_stalled_http_requests(
    stall_result: bool,
) -> None:
    transport = StalledAsyncReportTransport(stall_result=stall_result)
    async with AsyncVehicles(API_KEY, timeout=30, transport=transport) as client:
        with pytest.raises(VehiclesError) as raised:
            await asyncio.wait_for(
                client.history_reports.wait_for_result(REPORT_ID, max_wait=0.02),
                timeout=0.5,
            )

    assert raised.value.code == "report_wait_timeout"
    assert raised.value.retryable is True
    assert len(transport.observed_timeouts) == (2 if stall_result else 1)
    assert all(0 < timeout <= 0.02 for timeout in transport.observed_timeouts)


@pytest.mark.asyncio
async def test_async_wait_translates_late_api_error_to_deadline_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now, _ = install_async_clock(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        now[0] += request_timeout(request)
        return json_response({"code": "busy", "detail": "Try later."}, status=503)

    async with AsyncVehicles(API_KEY, timeout=30, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VehiclesError) as raised:
            await client.history_reports.wait_for_result(REPORT_ID, max_wait=3)

    assert raised.value.code == "report_wait_timeout"
