"""Zero-dependency sunset and golden-window calculation.

The knowledge base carries latitude/longitude per city; this module turns them
into a local sunset time with the standard NOAA approximation. The golden window
is the 120 minutes before sunset, matching the previous seasonal table's width
while fixing its city-agnostic error.

This is not an almanac: terrain, skyline, and atmospheric refraction beyond the
standard 90.833-degree horizon are not modelled. It is deterministic and close
enough to stop scheduling an outdoor shoot at 11:00.
"""

from __future__ import annotations

import math
from datetime import date

# China uses a single clock; a city-specific timezone would be a data field if
# this ever leaves the UTC+8 knowledge base.
DEFAULT_TIMEZONE_HOURS = 8.0
GOLDEN_LEAD_MINUTES = 120


def sunset_minutes(
    day: date,
    latitude: float,
    longitude: float,
    timezone_hours: float = DEFAULT_TIMEZONE_HOURS,
) -> int:
    """Local sunset in minutes after midnight (NOAA approximation)."""
    day_of_year = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (day_of_year - 1)
    equation_of_time = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    lat_rad = math.radians(latitude)
    cos_hour_angle = (
        math.cos(math.radians(90.833)) / (math.cos(lat_rad) * math.cos(declination))
        - math.tan(lat_rad) * math.tan(declination)
    )
    # Clamp for polar day/night; the current knowledge base never hits these.
    hour_angle = math.degrees(math.acos(max(-1.0, min(1.0, cos_hour_angle))))
    utc_minutes = 720 - 4 * (longitude - hour_angle) - equation_of_time
    return int(round(utc_minutes + timezone_hours * 60))


def golden_window(
    day: date,
    latitude: float,
    longitude: float,
    timezone_hours: float = DEFAULT_TIMEZONE_HOURS,
    lead_minutes: int = GOLDEN_LEAD_MINUTES,
) -> tuple[int, int]:
    """(start, end) of the golden window, in minutes after midnight."""
    sunset = sunset_minutes(day, latitude, longitude, timezone_hours)
    return max(0, sunset - lead_minutes), min(24 * 60, sunset)
