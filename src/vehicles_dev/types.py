"""Public response types for the Vehicles.dev API."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias, TypedDict

JsonObject: TypeAlias = dict[str, Any]
ListingOrder: TypeAlias = Literal["asc", "desc"]
ListingSort: TypeAlias = Literal["price", "miles", "year", "days_on_market"]
VehicleHistoryReportStatus: TypeAlias = Literal[
    "submitting", "queued", "processing", "action_required", "completed"
]


class InvalidParam(TypedDict):
    """One invalid input reported by an RFC 9457 problem document."""

    name: str
    pointer: str
    reason: str


class VinDecodeResult(TypedDict):
    origin: Literal["store", "vpic"]
    source: Literal["carscrape"]
    vehicle: JsonObject
    vin: str


class VehicleSpecifications(TypedDict):
    source: Literal["carscrape"]
    specifications: JsonObject
    vin: str


class VehicleRecalls(TypedDict):
    count: int
    make: str
    model: str
    recalls: list[JsonObject]
    source: Literal["carscrape"]
    vin: str
    year: int


class VehiclePhotos(TypedDict):
    listingSource: str | None
    listingUrl: str | None
    photoCount: int | None
    primaryImage: str
    source: Literal["carscrape"]
    vin: str


class VehicleListings(TypedDict):
    count: int
    limit: int
    offset: int
    results: list[JsonObject]
    source: Literal["carscrape"]
    total: int


class VehicleListingHistory(TypedDict):
    currentPrice: int | None
    currentlyActive: bool
    firstSeen: str
    lastSeen: str
    observations: list[JsonObject]
    priceChanges: int
    priceMax: int | None
    priceMin: int | None
    source: Literal["carscrape"]
    vin: str


class VehicleMarketValue(TypedDict):
    currency: str
    estimateUsd: int
    inputs: JsonObject
    medianApePct: float
    source: Literal["carscrape"]


class VehicleDepreciation(TypedDict):
    annualDecay: float | None
    byModelYear: list[JsonObject]
    curveByAge: list[JsonObject] | None
    make: str
    model: str
    source: Literal["carscrape"]


class VehicleOwnershipCosts(TypedDict):
    annualFuelCostUsd: int | None
    co2GramsPerMile: int | None
    combinedMpg: int | None
    config: str | None
    fiveYearFuelCostUsd: int | None
    fuelType: str | None
    make: str
    model: str
    note: str
    source: Literal["carscrape"]
    trimsAvailable: int
    year: int


class VehicleCompositeReport(TypedDict):
    coverage: list[str]
    depreciation: JsonObject | None
    generatedAt: str
    identity: JsonObject
    marketValue: JsonObject | None
    origin: Literal["store", "vpic"]
    source: Literal["carscrape"]
    vin: str


class VehicleHistoryReport(TypedDict):
    createdAt: str
    hasResult: bool
    id: str
    retryAfterSeconds: int
    status: VehicleHistoryReportStatus
    updatedAt: str
    vin: str


class CreatedVehicleHistoryReport(VehicleHistoryReport):
    replayed: bool


class VehicleHistoryReportResult(TypedDict):
    report: JsonObject


__all__ = [
    "CreatedVehicleHistoryReport",
    "InvalidParam",
    "JsonObject",
    "ListingOrder",
    "ListingSort",
    "VehicleCompositeReport",
    "VehicleDepreciation",
    "VehicleHistoryReport",
    "VehicleHistoryReportResult",
    "VehicleHistoryReportStatus",
    "VehicleListingHistory",
    "VehicleListings",
    "VehicleMarketValue",
    "VehicleOwnershipCosts",
    "VehiclePhotos",
    "VehicleRecalls",
    "VehicleSpecifications",
    "VinDecodeResult",
]
