"""Next-due calculation for automation schedules (documented timezone + DST-aware)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


def parse_schedule(schedule_json: str) -> dict[str, Any]:
    try:
        data = json.loads(schedule_json or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _combine_local(day: datetime, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    return local.astimezone(timezone.utc)


def compute_next_run_at(
    *,
    schedule_kind: str,
    schedule_json: str,
    timezone_name: str,
    from_time: datetime | None = None,
) -> datetime | None:
    """Return the next UTC due time after ``from_time`` (default: now)."""
    now = from_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    kind = (schedule_kind or "manual").lower()
    cfg = parse_schedule(schedule_json)
    tz = _tz(timezone_name)

    if kind in {"manual", "webhook"}:
        return None

    if kind == "once":
        raw = cfg.get("run_at")
        if not raw:
            return None
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz).astimezone(timezone.utc)
        return when if when > now else None

    if kind == "interval":
        minutes = int(cfg.get("interval_minutes") or 0)
        if minutes < 5 or minutes > 60 * 24 * 7:
            return None
        return now + timedelta(minutes=minutes)

    if kind in {"daily", "weekdays"}:
        time_str = str(cfg.get("time") or "09:00")
        try:
            hour_s, minute_s = time_str.split(":", 1)
            hour, minute = int(hour_s), int(minute_s)
        except ValueError:
            hour, minute = 9, 0
        local_now = now.astimezone(tz)
        weekdays = cfg.get("weekdays")
        if kind == "weekdays":
            # Monday=0 … Sunday=6
            allowed = set(int(x) for x in (weekdays or [0, 1, 2, 3, 4]))
        else:
            allowed = set(range(7))
        candidate_day = local_now.date()
        for _ in range(14):
            day_dt = datetime(candidate_day.year, candidate_day.month, candidate_day.day, tzinfo=tz)
            if day_dt.weekday() in allowed:
                due = _combine_local(day_dt, hour, minute, tz)
                if due > now:
                    return due
            candidate_day = candidate_day + timedelta(days=1)
        return None

    return None


def occurrence_key(automation_id: str, due_at: datetime) -> str:
    due = due_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return f"{automation_id}:{due}"
