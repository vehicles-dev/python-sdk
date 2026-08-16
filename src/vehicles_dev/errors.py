"""Exceptions raised by the Vehicles.dev SDK."""

from __future__ import annotations

from collections.abc import Iterable

from .types import InvalidParam


class VehiclesError(Exception):
    """An API problem, transport failure, or local report-workflow failure."""

    def __init__(
        self,
        *,
        code: str,
        detail: str,
        status: int | None = None,
        type: str | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        invalid_params: Iterable[InvalidParam] = (),
        retry_after_seconds: int | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.detail = detail
        self.type = type
        self.request_id = request_id
        self.retryable = retryable
        self.invalid_params = tuple(invalid_params)
        self.retry_after_seconds = retry_after_seconds
        prefix = "vehicles.dev error" if status is None else f"vehicles.dev API error {status}"
        super().__init__(f"{prefix} {code}: {detail}")

    def __repr__(self) -> str:
        return (
            f"VehiclesError(status={self.status!r}, code={self.code!r}, detail={self.detail!r}, "
            f"type={self.type!r}, request_id={self.request_id!r}, "
            f"retryable={self.retryable!r}, invalid_params={self.invalid_params!r}, "
            f"retry_after_seconds={self.retry_after_seconds!r})"
        )


__all__ = ["VehiclesError"]
