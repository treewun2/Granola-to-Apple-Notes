"""
schedule_logic.py — pure scheduling/formatting helpers for the Granola Sync
menu-bar app. Imports nothing beyond datetime, so it unit-tests anywhere
(no rumps/AppKit).

Semantics mirror the v1 launchd job: a "daily" schedule fires once per
scheduled instant, and a missed instant (Mac asleep, app not running)
fires as soon as the app next checks — launchd's "run on next wake".
"""

import datetime

MODES = ("off", "hourly", "every_4_hours", "daily")
INTERVAL_SECONDS = {"hourly": 60 * 60, "every_4_hours": 4 * 60 * 60}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def is_valid_daily_time(value) -> bool:
    """True if value is a 24-hour 'HH:MM' string."""
    try:
        h, m = str(value).strip().split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except ValueError:
        return False


def parse_daily_time(value, fallback=(8, 0)):
    """'HH:MM' -> (hour, minute). Junk input returns fallback — a bad
    config value must degrade to the v1 default (08:00), never crash."""
    if is_valid_daily_time(value):
        h, m = str(value).strip().split(":")
        return int(h), int(m)
    return fallback


def most_recent_daily_instant(now: datetime.datetime, hour: int,
                              minute: int) -> datetime.datetime:
    """The most recent occurrence of hour:minute at or before `now`."""
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > now:
        candidate -= datetime.timedelta(days=1)
    return candidate


def is_sync_due(schedule: dict, last_auto, now: datetime.datetime) -> bool:
    """Decide whether an automatic sync should fire.

    schedule:  the config "schedule" dict ({"mode": ..., "time": ...})
    last_auto: datetime of the last *scheduled* sync start, or None if
               the schedule has never fired (treated as overdue).
    """
    mode = (schedule or {}).get("mode", "off")
    if mode == "off" or mode not in MODES:
        return False  # unknown modes fail safe: no automatic syncing
    if last_auto is None:
        return True
    if mode == "daily":
        h, m = parse_daily_time((schedule or {}).get("time"))
        return last_auto < most_recent_daily_instant(now, h, m)
    return (now - last_auto).total_seconds() >= INTERVAL_SECONDS[mode]


def format_daily_time(hour: int, minute: int) -> str:
    """(17, 30) -> '5:30 PM' (no strftime — %-I is platform-dependent)."""
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    return f"{h12}:{minute:02d} {suffix}"


def describe_schedule(schedule: dict) -> str:
    """Menu-friendly description of the schedule config."""
    mode = (schedule or {}).get("mode", "off")
    if mode == "hourly":
        return "Every Hour"
    if mode == "every_4_hours":
        return "Every 4 Hours"
    if mode == "daily":
        h, m = parse_daily_time((schedule or {}).get("time"))
        return f"Daily at {format_daily_time(h, m)}"
    return "Off"


def friendly_timestamp(dt: datetime.datetime, now: datetime.datetime) -> str:
    """'today 2:14 PM' / 'yesterday 8:00 AM' / 'Jul 9, 8:00 AM'."""
    t = format_daily_time(dt.hour, dt.minute)
    if dt.date() == now.date():
        return f"today {t}"
    if dt.date() == now.date() - datetime.timedelta(days=1):
        return f"yesterday {t}"
    return f"{_MONTHS[dt.month - 1]} {dt.day}, {t}"
