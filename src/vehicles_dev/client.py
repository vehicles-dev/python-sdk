"""Sync and async Vehicles.dev API clients."""

from __future__ import annotations

import asyncio
import json
import math
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal, cast
from urllib.parse import quote, urlsplit

import httpx

from .errors import VehiclesError
from .types import (
    CreatedVehicleHistoryReport,
    InvalidParam,
    ListingOrder,
    ListingSort,
    VehicleCompositeReport,
    VehicleDepreciation,
    VehicleHistoryReport,
    VehicleHistoryReportResult,
    VehicleListingHistory,
    VehicleListings,
    VehicleMarketValue,
    VehicleOwnershipCosts,
    VehiclePhotos,
    VehicleRecalls,
    VehicleSpecifications,
    VinDecodeResult,
)

DEFAULT_BASE_URL = "https://api.vehicles.dev"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_WAIT = 300.0
USER_AGENT = "vehicles-dev-python/0.1.0"
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_NO_BODY = object()
_QueryValue = bool | float | int | str | None


def _monotonic() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _positive_seconds(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive number")
    return result


def _normalize_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise ValueError("base_url must be an absolute HTTP(S) URL") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    return value.rstrip("/")


def _normalize_api_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Vehicles requires a nonblank API key")
    return value.strip()


def _normalize_vin(vin: object) -> str:
    if not isinstance(vin, str):
        raise TypeError("VIN must be a string")
    normalized = vin.strip().upper()
    if not 1 <= len(normalized) <= 32:
        raise ValueError("VIN must contain between 1 and 32 characters")
    return normalized


def _normalize_history_vin(vin: object) -> str:
    if not isinstance(vin, str):
        raise TypeError("VIN must be a string")
    return vin.strip().upper()


def _vin_path(segment: str, vin: str) -> str:
    return f"/v1/vehicles/{segment}/{quote(_normalize_vin(vin), safe='')}"


def _report_path(report_id: object, suffix: str = "") -> str:
    if not isinstance(report_id, str):
        raise TypeError("report id must be a string")
    return f"/v1/vehicles/history-reports/{quote(report_id, safe='')}{suffix}"


def _validate_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or _UUID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "history_reports.create requires a caller-supplied stable UUID idempotency key"
        )
    return value


def _query_params(values: Mapping[str, _QueryValue] | None) -> tuple[tuple[str, str], ...]:
    params: list[tuple[str, str]] = []
    for key, value in (values or {}).items():
        if value is None:
            continue
        encoded = ("true" if value else "false") if isinstance(value, bool) else str(value)
        params.append((key, encoded))
    return tuple(params)


def _parse_retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return int(stripped)
    try:
        parsed = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0, math.ceil((parsed - datetime.now(UTC)).total_seconds()))


def _read_string(record: Mapping[object, object], key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) and value else None


def _read_invalid_params(record: Mapping[object, object]) -> tuple[InvalidParam, ...]:
    raw = record.get("invalid_params")
    if not isinstance(raw, list):
        return ()
    result: list[InvalidParam] = []
    for item in cast(list[object], raw):
        if not isinstance(item, Mapping):
            continue
        record = cast(Mapping[object, object], item)
        name = _read_string(record, "name")
        pointer = _read_string(record, "pointer")
        reason = _read_string(record, "reason")
        if name is not None and pointer is not None and reason is not None:
            result.append({"name": name, "pointer": pointer, "reason": reason})
    return tuple(result)


@dataclass(frozen=True, slots=True)
class _TransportResponse:
    data: object
    retry_after_seconds: int | None


class _ErrorFactory:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _redact(self, value: str) -> str:
        return value.replace(self._api_key, "[redacted]")

    def create(
        self,
        *,
        code: str,
        detail: str,
        status: int | None = None,
        type: str | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        invalid_params: tuple[InvalidParam, ...] = (),
        retry_after_seconds: int | None = None,
    ) -> VehiclesError:
        return VehiclesError(
            code=self._redact(code),
            detail=self._redact(detail),
            status=status,
            type=None if type is None else self._redact(type),
            request_id=None if request_id is None else self._redact(request_id),
            retryable=retryable,
            invalid_params=(
                {
                    "name": self._redact(item["name"]),
                    "pointer": self._redact(item["pointer"]),
                    "reason": self._redact(item["reason"]),
                }
                for item in invalid_params
            ),
            retry_after_seconds=retry_after_seconds,
        )


def _parse_response(
    *,
    content: bytes,
    status: int,
    headers: httpx.Headers,
    errors: _ErrorFactory,
) -> _TransportResponse:
    request_id_header = headers.get("x-request-id")
    retry_after_seconds = _parse_retry_after(headers.get("retry-after"))
    try:
        parsed = cast(object, json.loads(content))
    except (json.JSONDecodeError, UnicodeDecodeError):
        if 200 <= status < 300:
            raise errors.create(
                code="invalid_response_body",
                detail=f"The API returned {status} with a body that is not valid JSON.",
                status=status,
                request_id=request_id_header,
            ) from None
        raise errors.create(
            code="unexpected_response",
            detail=f"The API returned {status} without a valid problem document.",
            status=status,
            request_id=request_id_header,
            retryable=status == 429 or status >= 500,
            retry_after_seconds=retry_after_seconds,
        ) from None

    if 200 <= status < 300:
        return _TransportResponse(parsed, retry_after_seconds)
    if not isinstance(parsed, dict):
        raise errors.create(
            code="unexpected_response",
            detail=f"The API returned {status} without a valid problem document.",
            status=status,
            request_id=request_id_header,
            retryable=status == 429 or status >= 500,
            retry_after_seconds=retry_after_seconds,
        )

    problem = cast(Mapping[object, object], parsed)
    retryable_value = problem.get("retryable")
    raise errors.create(
        code=_read_string(problem, "code") or "unexpected_response",
        detail=(
            _read_string(problem, "detail")
            or _read_string(problem, "title")
            or "The API returned an error without details."
        ),
        status=status,
        type=_read_string(problem, "type"),
        request_id=_read_string(problem, "request_id") or request_id_header,
        retryable=(
            retryable_value if isinstance(retryable_value, bool) else status == 429 or status >= 500
        ),
        invalid_params=_read_invalid_params(problem),
        retry_after_seconds=retry_after_seconds,
    )


def _local_error(code: str, detail: str, *, retryable: bool = False) -> VehiclesError:
    return VehiclesError(code=code, detail=detail, retryable=retryable)


def _report_delay(report: Mapping[str, object]) -> float:
    value = report.get("retryAfterSeconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise _local_error(
            "invalid_report_state", "The vehicle history report returned an invalid retry cadence."
        )
    return float(value)


def _with_retry_after(data: object, retry_after_seconds: int | None) -> object:
    if retry_after_seconds is None or not isinstance(data, dict):
        return data
    result: dict[object, object] = dict(cast(Mapping[object, object], data))
    result["retryAfterSeconds"] = retry_after_seconds
    return result


class _SyncTransport:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._errors = _ErrorFactory(api_key)
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        query: Mapping[str, _QueryValue] | None = None,
        body: object = _NO_BODY,
        headers: Mapping[str, str] | None = None,
    ) -> _TransportResponse:
        if body is _NO_BODY:
            request = self._client.build_request(
                method,
                self._base_url + path,
                params=_query_params(query),
                headers=headers,
            )
        else:
            request = self._client.build_request(
                method,
                self._base_url + path,
                params=_query_params(query),
                headers=headers,
                json=body,
            )
        try:
            response = self._client.send(request, stream=True)
        except httpx.TimeoutException:
            raise self._errors.create(
                code="request_timeout",
                detail=(
                    f"The request to {self._base_url} did not complete within "
                    f"{self._timeout:g} seconds."
                ),
                retryable=True,
            ) from None
        except Exception as error:
            raise self._errors.create(
                code="network_unreachable",
                detail=f"The request to {self._base_url} could not be sent: {error!s}.",
                retryable=True,
            ) from None
        try:
            try:
                content = response.read()
            except httpx.TimeoutException:
                raise self._errors.create(
                    code="request_timeout",
                    detail=(
                        f"The request to {self._base_url} did not complete within "
                        f"{self._timeout:g} seconds."
                    ),
                    retryable=True,
                ) from None
            except Exception:
                raise self._errors.create(
                    code="response_unreadable",
                    detail="The response body could not be read.",
                    status=response.status_code,
                    request_id=response.headers.get("x-request-id"),
                    retryable=True,
                ) from None
            return _parse_response(
                content=content,
                status=response.status_code,
                headers=response.headers,
                errors=self._errors,
            )
        finally:
            response.close()


class _SyncHistoryReports:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def create(self, vin: str, *, idempotency_key: str) -> CreatedVehicleHistoryReport:
        response = self._transport.request(
            "POST",
            "/v1/vehicles/history-reports",
            body={"vin": _normalize_history_vin(vin)},
            headers={"Idempotency-Key": _validate_idempotency_key(idempotency_key)},
        )
        return cast(
            CreatedVehicleHistoryReport,
            _with_retry_after(response.data, response.retry_after_seconds),
        )

    def retry_submission(self, report_id: str) -> CreatedVehicleHistoryReport:
        response = self._transport.request("POST", _report_path(report_id, "/retry"))
        return cast(
            CreatedVehicleHistoryReport,
            _with_retry_after(response.data, response.retry_after_seconds),
        )

    def get_status(self, report_id: str) -> VehicleHistoryReport:
        response = self._transport.request("GET", _report_path(report_id))
        return cast(
            VehicleHistoryReport,
            _with_retry_after(response.data, response.retry_after_seconds),
        )

    def get_result(self, report_id: str) -> VehicleHistoryReportResult:
        response = self._transport.request("GET", _report_path(report_id, "/result"))
        return cast(VehicleHistoryReportResult, response.data)

    def wait_for_result(
        self,
        report_id: str,
        *,
        max_wait: float = DEFAULT_MAX_WAIT,
        poll_interval: float | None = None,
        abort_event: threading.Event | None = None,
    ) -> VehicleHistoryReportResult:
        max_wait = _positive_seconds(max_wait, "max_wait")
        if poll_interval is not None:
            poll_interval = _positive_seconds(poll_interval, "poll_interval")
        started_at = _monotonic()
        while True:
            self._check_abort(abort_event)
            self._check_deadline(started_at, max_wait)
            report = self.get_status(report_id)
            status = report.get("status")
            if status == "action_required":
                raise _local_error(
                    "report_action_required",
                    "The vehicle history report requires manual review before it can continue.",
                )
            if status == "completed":
                if report.get("hasResult") is not True:
                    raise _local_error(
                        "invalid_report_state",
                        "The vehicle history report completed without an available result.",
                    )
                try:
                    return self.get_result(report_id)
                except VehiclesError as error:
                    if error.status != 409 or error.code != "report_not_ready":
                        raise
                    delay = poll_interval or (
                        float(error.retry_after_seconds)
                        if error.retry_after_seconds is not None
                        else _report_delay(report)
                    )
                    self._wait(delay, started_at, max_wait, abort_event)
                    continue
            if status not in {"submitting", "queued", "processing"}:
                raise _local_error(
                    "invalid_report_state",
                    "The vehicle history report returned an unknown status.",
                )
            self._wait(
                poll_interval or _report_delay(cast(Mapping[str, object], report)),
                started_at,
                max_wait,
                abort_event,
            )

    @staticmethod
    def _check_abort(abort_event: threading.Event | None) -> None:
        if abort_event is not None and abort_event.is_set():
            raise _local_error("request_aborted", "The report wait was aborted.")

    @staticmethod
    def _check_deadline(started_at: float, max_wait: float) -> None:
        if _monotonic() - started_at >= max_wait:
            raise _local_error(
                "report_wait_timeout",
                f"The vehicle history report did not complete within {max_wait:g} seconds.",
                retryable=True,
            )

    def _wait(
        self,
        requested_delay: float,
        started_at: float,
        max_wait: float,
        abort_event: threading.Event | None,
    ) -> None:
        remaining = max_wait - (_monotonic() - started_at)
        if remaining <= 0:
            self._check_deadline(started_at, max_wait)
        delay = min(requested_delay, remaining)
        if abort_event is None:
            _sleep(delay)
        elif abort_event.wait(delay):
            raise _local_error("request_aborted", "The report wait was aborted.")


class Vehicles:
    """Synchronous, server-side client for the Vehicles.dev API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        normalized_key = _normalize_api_key(api_key)
        self._transport = _SyncTransport(
            api_key=normalized_key,
            base_url=_normalize_base_url(base_url),
            timeout=_positive_seconds(timeout, "timeout"),
            transport=transport,
        )
        self.history_reports = _SyncHistoryReports(self._transport)

    def __enter__(self) -> Vehicles:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self._transport.close()

    def _get(self, path: str, query: Mapping[str, _QueryValue] | None = None) -> object:
        return self._transport.request("GET", path, query=query).data

    def decode_vin(self, vin: str) -> VinDecodeResult:
        return cast(VinDecodeResult, self._get(_vin_path("vin", vin)))

    def get_specifications(self, vin: str) -> VehicleSpecifications:
        return cast(VehicleSpecifications, self._get(_vin_path("specifications", vin)))

    def get_recalls(self, vin: str) -> VehicleRecalls:
        return cast(VehicleRecalls, self._get(_vin_path("recalls", vin)))

    def get_photos(self, vin: str) -> VehiclePhotos:
        return cast(VehiclePhotos, self._get(_vin_path("photos", vin)))

    def search_listings(
        self,
        *,
        active: bool | None = None,
        condition: str | None = None,
        limit: int | None = None,
        make: str | None = None,
        mileage_max: int | None = None,
        min_quality: float | None = None,
        model: str | None = None,
        offset: int | None = None,
        order: ListingOrder | None = None,
        price_max: int | None = None,
        price_min: int | None = None,
        seller_type: str | None = None,
        sold: bool | None = None,
        sort: ListingSort | None = None,
        source: str | None = None,
        state: str | None = None,
        valid_vin: bool | None = None,
        year_max: int | None = None,
        year_min: int | None = None,
    ) -> VehicleListings:
        return cast(
            VehicleListings,
            self._get(
                "/v1/vehicles/listings",
                {
                    "active": active,
                    "condition": condition,
                    "limit": limit,
                    "make": make,
                    "mileage_max": mileage_max,
                    "min_quality": min_quality,
                    "model": model,
                    "offset": offset,
                    "order": order,
                    "price_max": price_max,
                    "price_min": price_min,
                    "seller_type": seller_type,
                    "sold": sold,
                    "sort": sort,
                    "source": source,
                    "state": state,
                    "valid_vin": valid_vin,
                    "year_max": year_max,
                    "year_min": year_min,
                },
            ),
        )

    def get_listing_history(self, vin: str) -> VehicleListingHistory:
        return cast(VehicleListingHistory, self._get(_vin_path("history", vin)))

    def get_market_value(
        self,
        *,
        year: int,
        make: str,
        model: str,
        base_msrp: int | None = None,
        body_style: str | None = None,
        color: str | None = None,
        condition: str | None = None,
        drivetrain: str | None = None,
        fuel: str | None = None,
        miles: int | None = None,
        state: str | None = None,
        transmission: str | None = None,
        trim: str | None = None,
    ) -> VehicleMarketValue:
        return cast(
            VehicleMarketValue,
            self._get(
                "/v1/vehicles/market-value",
                {
                    "base_msrp": base_msrp,
                    "body_style": body_style,
                    "color": color,
                    "condition": condition,
                    "drivetrain": drivetrain,
                    "fuel": fuel,
                    "make": make,
                    "miles": miles,
                    "model": model,
                    "state": state,
                    "transmission": transmission,
                    "trim": trim,
                    "year": year,
                },
            ),
        )

    def get_depreciation(self, *, make: str, model: str) -> VehicleDepreciation:
        return cast(
            VehicleDepreciation,
            self._get("/v1/vehicles/depreciation", {"make": make, "model": model}),
        )

    def get_ownership_costs(self, *, year: int, make: str, model: str) -> VehicleOwnershipCosts:
        return cast(
            VehicleOwnershipCosts,
            self._get(
                "/v1/vehicles/ownership-costs",
                {"make": make, "model": model, "year": year},
            ),
        )

    def get_composite_report(
        self, vin: str, *, miles: int | None = None, state: str | None = None
    ) -> VehicleCompositeReport:
        return cast(
            VehicleCompositeReport,
            self._get(_vin_path("report", vin), {"miles": miles, "state": state}),
        )


class _AsyncTransport:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._errors = _ErrorFactory(api_key)
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        query: Mapping[str, _QueryValue] | None = None,
        body: object = _NO_BODY,
        headers: Mapping[str, str] | None = None,
    ) -> _TransportResponse:
        if body is _NO_BODY:
            request = self._client.build_request(
                method,
                self._base_url + path,
                params=_query_params(query),
                headers=headers,
            )
        else:
            request = self._client.build_request(
                method,
                self._base_url + path,
                params=_query_params(query),
                headers=headers,
                json=body,
            )
        try:
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException:
            raise self._errors.create(
                code="request_timeout",
                detail=(
                    f"The request to {self._base_url} did not complete within "
                    f"{self._timeout:g} seconds."
                ),
                retryable=True,
            ) from None
        except Exception as error:
            raise self._errors.create(
                code="network_unreachable",
                detail=f"The request to {self._base_url} could not be sent: {error!s}.",
                retryable=True,
            ) from None
        try:
            try:
                content = await response.aread()
            except httpx.TimeoutException:
                raise self._errors.create(
                    code="request_timeout",
                    detail=(
                        f"The request to {self._base_url} did not complete within "
                        f"{self._timeout:g} seconds."
                    ),
                    retryable=True,
                ) from None
            except Exception:
                raise self._errors.create(
                    code="response_unreadable",
                    detail="The response body could not be read.",
                    status=response.status_code,
                    request_id=response.headers.get("x-request-id"),
                    retryable=True,
                ) from None
            return _parse_response(
                content=content,
                status=response.status_code,
                headers=response.headers,
                errors=self._errors,
            )
        finally:
            await response.aclose()


class _AsyncHistoryReports:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def create(self, vin: str, *, idempotency_key: str) -> CreatedVehicleHistoryReport:
        response = await self._transport.request(
            "POST",
            "/v1/vehicles/history-reports",
            body={"vin": _normalize_history_vin(vin)},
            headers={"Idempotency-Key": _validate_idempotency_key(idempotency_key)},
        )
        return cast(
            CreatedVehicleHistoryReport,
            _with_retry_after(response.data, response.retry_after_seconds),
        )

    async def retry_submission(self, report_id: str) -> CreatedVehicleHistoryReport:
        response = await self._transport.request("POST", _report_path(report_id, "/retry"))
        return cast(
            CreatedVehicleHistoryReport,
            _with_retry_after(response.data, response.retry_after_seconds),
        )

    async def get_status(self, report_id: str) -> VehicleHistoryReport:
        response = await self._transport.request("GET", _report_path(report_id))
        return cast(
            VehicleHistoryReport,
            _with_retry_after(response.data, response.retry_after_seconds),
        )

    async def get_result(self, report_id: str) -> VehicleHistoryReportResult:
        response = await self._transport.request("GET", _report_path(report_id, "/result"))
        return cast(VehicleHistoryReportResult, response.data)

    async def wait_for_result(
        self,
        report_id: str,
        *,
        max_wait: float = DEFAULT_MAX_WAIT,
        poll_interval: float | None = None,
        abort_event: asyncio.Event | None = None,
    ) -> VehicleHistoryReportResult:
        max_wait = _positive_seconds(max_wait, "max_wait")
        if poll_interval is not None:
            poll_interval = _positive_seconds(poll_interval, "poll_interval")
        started_at = _monotonic()
        while True:
            self._check_abort(abort_event)
            self._check_deadline(started_at, max_wait)
            report = await self.get_status(report_id)
            status = report.get("status")
            if status == "action_required":
                raise _local_error(
                    "report_action_required",
                    "The vehicle history report requires manual review before it can continue.",
                )
            if status == "completed":
                if report.get("hasResult") is not True:
                    raise _local_error(
                        "invalid_report_state",
                        "The vehicle history report completed without an available result.",
                    )
                try:
                    return await self.get_result(report_id)
                except VehiclesError as error:
                    if error.status != 409 or error.code != "report_not_ready":
                        raise
                    delay = poll_interval or (
                        float(error.retry_after_seconds)
                        if error.retry_after_seconds is not None
                        else _report_delay(report)
                    )
                    await self._wait(delay, started_at, max_wait, abort_event)
                    continue
            if status not in {"submitting", "queued", "processing"}:
                raise _local_error(
                    "invalid_report_state",
                    "The vehicle history report returned an unknown status.",
                )
            await self._wait(
                poll_interval or _report_delay(cast(Mapping[str, object], report)),
                started_at,
                max_wait,
                abort_event,
            )

    @staticmethod
    def _check_abort(abort_event: asyncio.Event | None) -> None:
        if abort_event is not None and abort_event.is_set():
            raise _local_error("request_aborted", "The report wait was aborted.")

    @staticmethod
    def _check_deadline(started_at: float, max_wait: float) -> None:
        if _monotonic() - started_at >= max_wait:
            raise _local_error(
                "report_wait_timeout",
                f"The vehicle history report did not complete within {max_wait:g} seconds.",
                retryable=True,
            )

    async def _wait(
        self,
        requested_delay: float,
        started_at: float,
        max_wait: float,
        abort_event: asyncio.Event | None,
    ) -> None:
        remaining = max_wait - (_monotonic() - started_at)
        if remaining <= 0:
            self._check_deadline(started_at, max_wait)
        delay = min(requested_delay, remaining)
        if abort_event is None:
            await _async_sleep(delay)
            return
        try:
            await asyncio.wait_for(abort_event.wait(), timeout=delay)
        except TimeoutError:
            return
        raise _local_error("request_aborted", "The report wait was aborted.")


class AsyncVehicles:
    """Asynchronous, server-side client for the Vehicles.dev API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_key = _normalize_api_key(api_key)
        self._transport = _AsyncTransport(
            api_key=normalized_key,
            base_url=_normalize_base_url(base_url),
            timeout=_positive_seconds(timeout, "timeout"),
            transport=transport,
        )
        self.history_reports = _AsyncHistoryReports(self._transport)

    async def __aenter__(self) -> AsyncVehicles:
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def _get(self, path: str, query: Mapping[str, _QueryValue] | None = None) -> object:
        return (await self._transport.request("GET", path, query=query)).data

    async def decode_vin(self, vin: str) -> VinDecodeResult:
        return cast(VinDecodeResult, await self._get(_vin_path("vin", vin)))

    async def get_specifications(self, vin: str) -> VehicleSpecifications:
        return cast(VehicleSpecifications, await self._get(_vin_path("specifications", vin)))

    async def get_recalls(self, vin: str) -> VehicleRecalls:
        return cast(VehicleRecalls, await self._get(_vin_path("recalls", vin)))

    async def get_photos(self, vin: str) -> VehiclePhotos:
        return cast(VehiclePhotos, await self._get(_vin_path("photos", vin)))

    async def search_listings(
        self,
        *,
        active: bool | None = None,
        condition: str | None = None,
        limit: int | None = None,
        make: str | None = None,
        mileage_max: int | None = None,
        min_quality: float | None = None,
        model: str | None = None,
        offset: int | None = None,
        order: ListingOrder | None = None,
        price_max: int | None = None,
        price_min: int | None = None,
        seller_type: str | None = None,
        sold: bool | None = None,
        sort: ListingSort | None = None,
        source: str | None = None,
        state: str | None = None,
        valid_vin: bool | None = None,
        year_max: int | None = None,
        year_min: int | None = None,
    ) -> VehicleListings:
        return cast(
            VehicleListings,
            await self._get(
                "/v1/vehicles/listings",
                {
                    "active": active,
                    "condition": condition,
                    "limit": limit,
                    "make": make,
                    "mileage_max": mileage_max,
                    "min_quality": min_quality,
                    "model": model,
                    "offset": offset,
                    "order": order,
                    "price_max": price_max,
                    "price_min": price_min,
                    "seller_type": seller_type,
                    "sold": sold,
                    "sort": sort,
                    "source": source,
                    "state": state,
                    "valid_vin": valid_vin,
                    "year_max": year_max,
                    "year_min": year_min,
                },
            ),
        )

    async def get_listing_history(self, vin: str) -> VehicleListingHistory:
        return cast(VehicleListingHistory, await self._get(_vin_path("history", vin)))

    async def get_market_value(
        self,
        *,
        year: int,
        make: str,
        model: str,
        base_msrp: int | None = None,
        body_style: str | None = None,
        color: str | None = None,
        condition: str | None = None,
        drivetrain: str | None = None,
        fuel: str | None = None,
        miles: int | None = None,
        state: str | None = None,
        transmission: str | None = None,
        trim: str | None = None,
    ) -> VehicleMarketValue:
        return cast(
            VehicleMarketValue,
            await self._get(
                "/v1/vehicles/market-value",
                {
                    "base_msrp": base_msrp,
                    "body_style": body_style,
                    "color": color,
                    "condition": condition,
                    "drivetrain": drivetrain,
                    "fuel": fuel,
                    "make": make,
                    "miles": miles,
                    "model": model,
                    "state": state,
                    "transmission": transmission,
                    "trim": trim,
                    "year": year,
                },
            ),
        )

    async def get_depreciation(self, *, make: str, model: str) -> VehicleDepreciation:
        return cast(
            VehicleDepreciation,
            await self._get("/v1/vehicles/depreciation", {"make": make, "model": model}),
        )

    async def get_ownership_costs(
        self, *, year: int, make: str, model: str
    ) -> VehicleOwnershipCosts:
        return cast(
            VehicleOwnershipCosts,
            await self._get(
                "/v1/vehicles/ownership-costs",
                {"make": make, "model": model, "year": year},
            ),
        )

    async def get_composite_report(
        self, vin: str, *, miles: int | None = None, state: str | None = None
    ) -> VehicleCompositeReport:
        return cast(
            VehicleCompositeReport,
            await self._get(_vin_path("report", vin), {"miles": miles, "state": state}),
        )


__all__ = ["AsyncVehicles", "Vehicles"]
