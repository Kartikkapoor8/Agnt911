"""Straight-line distance on the Earth's surface. Shared by agents, feed, and protocol."""

import math

from .models import Location

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(a: Location, b: Location) -> float:
    """Return the great-circle distance in metres between two locations."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))
