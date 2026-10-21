# ADR 0003: Depend on tzdata for IANA timezones on Windows

## Status

Accepted.

## Context

`get_current_time` uses `zoneinfo.ZoneInfo` with IANA names such as `Asia/Calcutta`. On many Windows Python installs, the system timezone database is not available to `zoneinfo`, so lookups raise `ZoneInfoNotFoundError` even for valid names.

## Decision

Add the `tzdata` package to `backend/requirements.txt` so timezone behavior is portable across developer machines.

## Consequences

- Deterministic timezone-aware timestamps in mock mode and tests.
- Slight dependency growth in exchange for correct local-time semantics.
