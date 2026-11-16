"""Is the market open? The manual override wins; otherwise the weekly schedule decides."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app import db

# Default market time zone, matching MARKET_TIMEZONE's default in app/config.py.
DEFAULT_MARKET_TIMEZONE = "America/New_York"

# datetime.weekday(): 0 = Monday ... 6 = Sunday.
_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _parse_time_of_day(value):
    """Turn a stored ``"HH:MM:SS"`` market-hours string into a ``datetime.time``."""
    return datetime.strptime(value, "%H:%M:%S").time()


def is_market_open(conn, now, tz_name=DEFAULT_MARKET_TIMEZONE):
    """Return True if the market is open at the moment ``now``, else False."""
    # Step 1: the administrator's manual override beats the schedule.
    settings = db.get_market_settings(conn)
    override_mode = settings["override_mode"] if settings is not None else "none"
    if override_mode == "force_open":
        return True
    if override_mode == "force_closed":
        return False

    # Step 2: no override, so check today's schedule in the market's time zone.
    local_now = now.astimezone(ZoneInfo(tz_name))
    weekday_name = _WEEKDAY_NAMES[local_now.weekday()]

    # No row, or a day marked closed all day (is_holiday = 1), means closed.
    schedule = db.get_schedule_for_day(conn, weekday_name)
    if schedule is None or int(schedule["is_holiday"]) == 1:
        return False

    # Open from the opening time up to (but not including) the closing time.
    open_time = _parse_time_of_day(schedule["market_open_time"])
    close_time = _parse_time_of_day(schedule["market_close_time"])
    local_time = local_now.time()
    return open_time <= local_time < close_time
