from __future__ import annotations

import json

import httpx
import pytest
from conftest import API_KEY, BASE_URL, IDEMPOTENCY_KEY, REPORT_ID, VIN, json_response

from vehicles_dev import AsyncVehicles, Vehicles, VehiclesError


def _assert_common_headers(request: httpx.Request) -> None:
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["accept"] == "application/json"
    assert request.headers["user-agent"] == "vehicles-dev-python/0.1.1"
    assert "cookie" not in request.headers
    assert "origin" not in request.headers


def test_sync_maps_all_twelve_published_routes_and_wire_values() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response({})

    with Vehicles(
        API_KEY, base_url=f"{BASE_URL}/gateway/", transport=httpx.MockTransport(handler)
    ) as client:
        client.decode_vin(VIN.lower())
        client.get_specifications(VIN)
        client.get_recalls(VIN)
        client.get_photos(VIN)
        client.search_listings(
            active=False,
            limit=25,
            min_quality=0.75,
            sort="days_on_market",
            valid_vin=True,
            year_min=None,
        )
        client.get_market_value(
            year=2022,
            make="Honda",
            model="Accord",
            base_msrp=28_000,
            body_style="Sedan",
            miles=12_345,
            state="CA",
        )
        client.get_depreciation(make="Honda", model="Accord")
        client.get_ownership_costs(year=2022, make="Honda", model="Accord")
        client.history_reports.create(VIN.lower(), idempotency_key=IDEMPOTENCY_KEY)
        client.history_reports.retry_submission("report/with space")
        client.history_reports.get_status(REPORT_ID)
        client.history_reports.get_result(REPORT_ID)

        assert not hasattr(client, "get_listing_history")
        assert not hasattr(client, "get_composite_report")

    assert [request.method for request in requests] == ["GET"] * 8 + ["POST", "POST", "GET", "GET"]
    assert [request.url.path for request in requests] == [
        f"/gateway/v1/vehicles/vin/{VIN}",
        f"/gateway/v1/vehicles/specifications/{VIN}",
        f"/gateway/v1/vehicles/recalls/{VIN}",
        f"/gateway/v1/vehicles/photos/{VIN}",
        "/gateway/v1/vehicles/listings",
        "/gateway/v1/vehicles/market-value",
        "/gateway/v1/vehicles/depreciation",
        "/gateway/v1/vehicles/ownership-costs",
        "/gateway/v1/vehicles/history-reports",
        "/gateway/v1/vehicles/history-reports/report/with space/retry",
        f"/gateway/v1/vehicles/history-reports/{REPORT_ID}",
        f"/gateway/v1/vehicles/history-reports/{REPORT_ID}/result",
    ]
    assert "%2F" in str(requests[9].url) and "%20" in str(requests[9].url)
    assert dict(requests[4].url.params) == {
        "active": "false",
        "limit": "25",
        "min_quality": "0.75",
        "sort": "days_on_market",
        "valid_vin": "true",
    }
    assert dict(requests[5].url.params) == {
        "base_msrp": "28000",
        "body_style": "Sedan",
        "make": "Honda",
        "miles": "12345",
        "model": "Accord",
        "state": "CA",
        "year": "2022",
    }
    assert dict(requests[6].url.params) == {"make": "Honda", "model": "Accord"}
    assert dict(requests[7].url.params) == {
        "make": "Honda",
        "model": "Accord",
        "year": "2022",
    }
    assert json.loads(requests[8].content) == {"vin": VIN}
    assert requests[8].headers["idempotency-key"] == IDEMPOTENCY_KEY
    assert requests[8].headers["content-type"] == "application/json"
    assert "content-type" not in requests[9].headers
    for request in requests:
        _assert_common_headers(request)


@pytest.mark.asyncio
async def test_async_maps_all_twelve_published_routes_with_sync_parity() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response({})

    async with AsyncVehicles(
        API_KEY,
        base_url=f"{BASE_URL}/proxy",
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.decode_vin(VIN.lower())
        await client.get_specifications(VIN)
        await client.get_recalls(VIN)
        await client.get_photos(VIN)
        await client.search_listings(active=True, sold=False)
        await client.get_market_value(year=2020, make="Ford", model="F-150")
        await client.get_depreciation(make="Ford", model="F-150")
        await client.get_ownership_costs(year=2020, make="Ford", model="F-150")
        await client.history_reports.create(VIN, idempotency_key=IDEMPOTENCY_KEY)
        await client.history_reports.retry_submission(REPORT_ID)
        await client.history_reports.get_status(REPORT_ID)
        await client.history_reports.get_result(REPORT_ID)

        assert not hasattr(client, "get_listing_history")
        assert not hasattr(client, "get_composite_report")

    assert [request.method for request in requests] == ["GET"] * 8 + ["POST", "POST", "GET", "GET"]
    assert [request.url.path.removeprefix("/proxy") for request in requests] == [
        f"/v1/vehicles/vin/{VIN}",
        f"/v1/vehicles/specifications/{VIN}",
        f"/v1/vehicles/recalls/{VIN}",
        f"/v1/vehicles/photos/{VIN}",
        "/v1/vehicles/listings",
        "/v1/vehicles/market-value",
        "/v1/vehicles/depreciation",
        "/v1/vehicles/ownership-costs",
        "/v1/vehicles/history-reports",
        f"/v1/vehicles/history-reports/{REPORT_ID}/retry",
        f"/v1/vehicles/history-reports/{REPORT_ID}",
        f"/v1/vehicles/history-reports/{REPORT_ID}/result",
    ]
    assert dict(requests[4].url.params) == {"active": "true", "sold": "false"}
    assert dict(requests[5].url.params) == {"make": "Ford", "model": "F-150", "year": "2020"}
    for request in requests:
        _assert_common_headers(request)


@pytest.mark.parametrize(
    "base_url",
    ["", "/relative", "ftp://example.test", "https:///missing-host", "https://example.test?q=1"],
)
def test_rejects_invalid_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match=r"absolute HTTP\(S\)"):
        Vehicles(API_KEY, base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.test",
        "http://192.168.1.10",
        "http://127.example.test",
        "http://[::2]",
        "http://localhost.example.test",
    ],
)
def test_sync_rejects_non_loopback_http_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS or HTTP loopback"):
        Vehicles(API_KEY, base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://gateway.example.test/prefix",
        "http://localhost:8080/prefix",
        "http://127.0.0.1",
        "http://127.255.255.254:9000",
        "http://[::1]:8080",
    ],
)
def test_sync_allows_https_and_explicit_loopback_http(base_url: str) -> None:
    with Vehicles(
        API_KEY,
        base_url=base_url,
        transport=httpx.MockTransport(lambda request: json_response({})),
    ):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["http://example.test", "http://10.0.0.4", "http://[::2]"])
async def test_async_rejects_non_loopback_http_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS or HTTP loopback"):
        AsyncVehicles(API_KEY, base_url=base_url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url", ["https://gateway.example.test", "http://localhost", "http://[::1]"]
)
async def test_async_allows_https_and_explicit_loopback_http(base_url: str) -> None:
    async with AsyncVehicles(
        API_KEY,
        base_url=base_url,
        transport=httpx.MockTransport(lambda request: json_response({})),
    ):
        pass


@pytest.mark.parametrize("api_key", ["", "  ", None])
def test_requires_a_nonblank_api_key(api_key: str | None) -> None:
    with pytest.raises(ValueError, match="nonblank API key"):
        Vehicles(api_key)  # type: ignore[arg-type]


def test_trims_and_canonicalizes_lowercase_immediate_vin() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response({})

    with Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        client.decode_vin(f" {VIN.lower()} ")

    assert requests[0].url.path == f"/v1/vehicles/vin/{VIN}"


@pytest.mark.parametrize(
    "vin",
    [
        "A" * 16,
        "A" * 18,
        "1HGCM826I3A004352",
        "1HGCM826O3A004352",
        "1HGCM826Q3A004352",
    ],
)
def test_rejects_invalid_immediate_vins_but_defers_history_vin_validation(vin: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response({})

    with Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="exactly 17 characters"):
            client.decode_vin(vin)
        client.history_reports.create(" short ", idempotency_key=IDEMPOTENCY_KEY)

    assert json.loads(requests[0].content) == {"vin": "SHORT"}


def test_never_follows_redirects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://elsewhere.test/stolen"})

    with (
        Vehicles(API_KEY, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(VehiclesError) as raised,
    ):
        client.decode_vin(VIN)

    assert raised.value.status == 307
    assert len(requests) == 1
