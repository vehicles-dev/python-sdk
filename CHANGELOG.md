# Changelog

All notable changes to this project will be documented in this file.

## [0.1.1] - 2026-08-17

### Fixed

- Aligned immediate-response VIN validation with the API's exact 17-character VIN-safe contract,
  including lowercase canonicalization and rejection of I, O, and Q.
- Corrected the quickstart decode example to read the wire-format `year` field.

## [0.1.0] - 2026-08-17

### Added

- Fully typed synchronous `Vehicles` and asynchronous `AsyncVehicles` clients with eight vehicle-data
  operations.
- Durable vehicle-history report creation, explicit submission retry, status, result, and polling
  helpers.
- RFC 9457 `VehiclesError` mapping, timeout and network handling, and API-key redaction.
- Hatchling packaging for the `vehicles-dev` distribution with a `py.typed` marker.
