"""D1.1 / PC-1: the time rule is a pure function of (from_hour, now), so its boundaries are tested
with fixed moments, no clock and no running stand. The API part is in test_lr1.py (test_8)."""

import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # web/server, for `app.rules`

from app.rules import booking_start_error, current_hour  # noqa: E402

KYIV_SUMMER = timezone(timedelta(hours=3))
NOW = datetime(2027, 6, 15, 18, 40, 15, tzinfo=KYIV_SUMMER)  # 18:40:15 local = 15:40:15 UTC


def test_current_hour_is_truncated():
    assert current_hour(NOW) == datetime(2027, 6, 15, 18, 0, tzinfo=KYIV_SUMMER)


def test_running_hour_may_be_booked():
    assert booking_start_error(datetime(2027, 6, 15, 18, 0, tzinfo=KYIV_SUMMER), NOW) is None


def test_future_hour_may_be_booked():
    assert booking_start_error(datetime(2027, 6, 15, 19, 0, tzinfo=KYIV_SUMMER), NOW) is None


def test_hour_that_just_ended_is_rejected():
    assert booking_start_error(datetime(2027, 6, 15, 17, 0, tzinfo=KYIV_SUMMER), NOW)


def test_yesterday_is_rejected():
    assert booking_start_error(datetime(2027, 6, 14, 18, 0, tzinfo=KYIV_SUMMER), NOW)


def test_same_instant_in_another_zone():
    # 15:00 UTC is 18:00 in Kyiv: the running hour, allowed whatever offset the client sent
    assert booking_start_error(datetime(2027, 6, 15, 15, 0, tzinfo=UTC), NOW) is None
    assert booking_start_error(datetime(2027, 6, 15, 14, 0, tzinfo=UTC), NOW)


def test_exact_start_of_hour_boundary():
    at_start = datetime(2027, 6, 15, 18, 0, tzinfo=KYIV_SUMMER)
    assert booking_start_error(at_start, at_start) is None
    assert booking_start_error(at_start - timedelta(hours=1), at_start)
