"""Time rules of the parking topic as pure functions: the current time is a parameter, never read inside.

The route passes the server clock; tests pass any moment they need, so the boundaries
(the current hour, the hour that has just ended) are checked without waiting for the clock.
"""

from datetime import datetime, timedelta

HOUR = timedelta(hours=1)


def current_hour(now: datetime) -> datetime:
    """Start of the hour that contains `now` (whole-hour zones: the same instant in any of them)."""
    return now.replace(minute=0, second=0, microsecond=0)


def booking_start_error(from_hour: datetime, now: datetime) -> str | None:
    """None when a booking may start at `from_hour`, else the reason.

    The hour that is running now may still be booked: a started hour counts as a full one
    (the topic card), and entry is possible until its end. Any earlier hour has already ended.
    """
    if from_hour < current_hour(now):
        return "must not be in the past: the earliest start is the current hour"
    return None
