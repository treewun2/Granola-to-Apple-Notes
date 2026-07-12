"""Unit tests for the app's pure scheduling logic (no rumps/AppKit needed)."""

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import schedule_logic as sl  # noqa: E402


def dt(*args):
    return datetime.datetime(*args)


class DailyTimeParsingTests(unittest.TestCase):
    def test_valid_times(self):
        for value in ("00:00", "08:00", "8:05", "23:59"):
            self.assertTrue(sl.is_valid_daily_time(value), value)

    def test_invalid_times(self):
        for value in ("24:00", "8:60", "8", "8:5:3", "noon", "", None):
            self.assertFalse(sl.is_valid_daily_time(value), value)

    def test_parse(self):
        self.assertEqual(sl.parse_daily_time("17:30"), (17, 30))

    def test_parse_junk_falls_back_to_v1_default(self):
        self.assertEqual(sl.parse_daily_time("garbage"), (8, 0))
        self.assertEqual(sl.parse_daily_time(None), (8, 0))


class MostRecentDailyInstantTests(unittest.TestCase):
    def test_earlier_today(self):
        self.assertEqual(
            sl.most_recent_daily_instant(dt(2026, 7, 11, 9, 30), 8, 0),
            dt(2026, 7, 11, 8, 0))

    def test_not_yet_today_uses_yesterday(self):
        self.assertEqual(
            sl.most_recent_daily_instant(dt(2026, 7, 11, 7, 59), 8, 0),
            dt(2026, 7, 10, 8, 0))

    def test_exactly_now_counts_as_today(self):
        self.assertEqual(
            sl.most_recent_daily_instant(dt(2026, 7, 11, 8, 0), 8, 0),
            dt(2026, 7, 11, 8, 0))


class IsSyncDueTests(unittest.TestCase):
    DAILY = {"mode": "daily", "time": "08:00"}

    def test_off_is_never_due(self):
        self.assertFalse(sl.is_sync_due({"mode": "off"}, None,
                                        dt(2026, 7, 11, 12, 0)))

    def test_unknown_mode_fails_safe(self):
        self.assertFalse(sl.is_sync_due({"mode": "warp_speed"}, None,
                                        dt(2026, 7, 11, 12, 0)))

    def test_never_synced_is_due(self):
        self.assertTrue(sl.is_sync_due(self.DAILY, None,
                                       dt(2026, 7, 11, 12, 0)))

    def test_daily_due_after_scheduled_time(self):
        last = dt(2026, 7, 10, 8, 0)
        self.assertTrue(sl.is_sync_due(self.DAILY, last, dt(2026, 7, 11, 8, 1)))

    def test_daily_not_due_if_already_ran_today(self):
        last = dt(2026, 7, 11, 8, 0)
        self.assertFalse(sl.is_sync_due(self.DAILY, last, dt(2026, 7, 11, 9, 0)))

    def test_daily_armed_before_todays_time_fires_today(self):
        armed = dt(2026, 7, 11, 7, 0)  # user enabled schedule at 7:00
        self.assertTrue(sl.is_sync_due(self.DAILY, armed, dt(2026, 7, 11, 8, 1)))

    def test_daily_armed_after_todays_time_waits_for_tomorrow(self):
        armed = dt(2026, 7, 11, 14, 0)  # enabled at 2 PM, sync time 8 AM
        self.assertFalse(sl.is_sync_due(self.DAILY, armed, dt(2026, 7, 11, 15, 0)))
        self.assertTrue(sl.is_sync_due(self.DAILY, armed, dt(2026, 7, 12, 8, 0)))

    def test_daily_missed_while_asleep_runs_on_wake(self):
        last = dt(2026, 7, 10, 8, 0)
        self.assertTrue(sl.is_sync_due(self.DAILY, last, dt(2026, 7, 11, 11, 23)))

    def test_hourly(self):
        sched = {"mode": "hourly"}
        last = dt(2026, 7, 11, 10, 0)
        self.assertFalse(sl.is_sync_due(sched, last, dt(2026, 7, 11, 10, 59)))
        self.assertTrue(sl.is_sync_due(sched, last, dt(2026, 7, 11, 11, 0)))

    def test_every_4_hours(self):
        sched = {"mode": "every_4_hours"}
        last = dt(2026, 7, 11, 8, 0)
        self.assertFalse(sl.is_sync_due(sched, last, dt(2026, 7, 11, 11, 59)))
        self.assertTrue(sl.is_sync_due(sched, last, dt(2026, 7, 11, 12, 0)))

    def test_missing_schedule_dict_is_off(self):
        self.assertFalse(sl.is_sync_due(None, None, dt(2026, 7, 11, 12, 0)))


class FormattingTests(unittest.TestCase):
    def test_format_daily_time(self):
        self.assertEqual(sl.format_daily_time(0, 5), "12:05 AM")
        self.assertEqual(sl.format_daily_time(8, 0), "8:00 AM")
        self.assertEqual(sl.format_daily_time(12, 0), "12:00 PM")
        self.assertEqual(sl.format_daily_time(17, 30), "5:30 PM")

    def test_describe_schedule(self):
        self.assertEqual(sl.describe_schedule({"mode": "off"}), "Off")
        self.assertEqual(sl.describe_schedule({"mode": "hourly"}), "Every Hour")
        self.assertEqual(
            sl.describe_schedule({"mode": "daily", "time": "17:30"}),
            "Daily at 5:30 PM")

    def test_friendly_timestamp(self):
        now = dt(2026, 7, 11, 15, 0)
        self.assertEqual(sl.friendly_timestamp(dt(2026, 7, 11, 14, 14), now),
                         "today 2:14 PM")
        self.assertEqual(sl.friendly_timestamp(dt(2026, 7, 10, 8, 0), now),
                         "yesterday 8:00 AM")
        self.assertEqual(sl.friendly_timestamp(dt(2026, 7, 1, 8, 0), now),
                         "Jul 1, 8:00 AM")


if __name__ == "__main__":
    unittest.main()
