from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

API_KEY = "test"
BASE_URL = "https://example.test"
VIN = "1HGCM82633A004352"
REPORT_ID = "550e8400-e29b-41d4-a716-446655440000"
IDEMPOTENCY_KEY = "0198b8dc-3cf0-7e30-8fa1-000000000001"


def json_response(
    payload: Any,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


Handler = Callable[[httpx.Request], httpx.Response]
