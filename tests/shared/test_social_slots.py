from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.scheduling import SCHEDULE_TZ
from shared.social_slots import open_slots

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=SCHEDULE_TZ).astimezone(timezone.utc)  # Mon noon PST


def _local(slots):
    return [s.astimezone(SCHEDULE_TZ).strftime("%a %H:%M") for s in slots]


def test_week_has_nine_slots(profile):
    assert _local(open_slots(profile, now=NOW, taken=[])) == [
        "Mon 19:00", "Tue 19:00", "Wed 19:00", "Thu 19:00", "Fri 19:00",
        "Sat 10:00", "Sat 15:00", "Sun 10:00", "Sun 15:00",
    ]


def test_weekday_time_override(profile):
    slots = _local(open_slots(profile, now=NOW, taken=[], weekday_time="20:30"))
    assert slots[:5] == [f"{d} 20:30" for d in ("Mon", "Tue", "Wed", "Thu", "Fri")]


@pytest.mark.parametrize("bad", ["17:30", "21:30", "19:15", "21:00", "nope"])
def test_weekday_time_outside_window_rejected(profile, bad):
    with pytest.raises(ValueError):
        open_slots(profile, now=NOW, taken=[], weekday_time=bad)


def test_window_edges_allowed(profile):
    open_slots(profile, now=NOW, taken=[], weekday_time="18:00")
    open_slots(profile, now=NOW, taken=[], weekday_time="20:30")


def test_min_lead(profile):
    now = datetime(2026, 11, 16, 18, 30, tzinfo=SCHEDULE_TZ).astimezone(timezone.utc)
    assert _local(open_slots(profile, now=now, taken=[]))[0] == "Tue 19:00"


def test_taken_slot_excluded(profile):
    tue = datetime(2026, 11, 17, 19, 0, tzinfo=SCHEDULE_TZ).astimezone(timezone.utc)
    assert "Tue 19:00" not in _local(open_slots(profile, now=NOW, taken=[tue]))
    wed = datetime(2026, 11, 18, 19, 0, tzinfo=SCHEDULE_TZ).astimezone(timezone.utc)
    near = _local(open_slots(profile, now=NOW, taken=[wed + timedelta(minutes=29)]))
    assert "Wed 19:00" not in near
    edge = _local(open_slots(profile, now=NOW, taken=[wed + timedelta(minutes=30)]))
    assert "Wed 19:00" in edge
