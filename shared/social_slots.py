"""Open posting slots for a business, from its profile's slot rules (America/Los_Angeles)."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from shared.scheduling import SCHEDULE_TZ

TAKEN_WINDOW = timedelta(minutes=30)


def _hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _weekday_time(cfg: dict, override: str | None) -> time:
    if override is None:
        return _hhmm(cfg["weekdayDefault"])
    try:
        chosen = _hhmm(override)
    except ValueError:
        raise ValueError(f"weekday_time {override!r} must be HH:MM.") from None
    start, end = (_hhmm(v) for v in cfg["weekdayWindow"])
    if chosen.minute not in (0, 30) or not start <= chosen < end:
        raise ValueError(
            f"weekday_time {override} must be on the hour or half hour from {cfg['weekdayWindow'][0]} "
            f"to before {cfg['weekdayWindow'][1]}."
        )
    return chosen


def open_slots(profile: dict, *, now: datetime, taken: list[datetime], weekday_time: str | None = None) -> list[datetime]:
    cfg = profile["slots"]
    weekday = _weekday_time(cfg, weekday_time)
    weekend = [_hhmm(v) for v in cfg["weekend"]]
    earliest = now + timedelta(minutes=cfg["minLeadMinutes"])
    latest = now + timedelta(days=cfg["horizonDays"])
    today = now.astimezone(SCHEDULE_TZ).date()
    slots: list[datetime] = []
    for offset in range(cfg["horizonDays"] + 1):
        day = today + timedelta(days=offset)
        for t in ([weekday] if day.weekday() < 5 else weekend):
            slot = datetime.combine(day, t, tzinfo=SCHEDULE_TZ).astimezone(timezone.utc)
            if earliest <= slot <= latest and all(abs(slot - other) >= TAKEN_WINDOW for other in taken):
                slots.append(slot)
    return sorted(slots)
