"""
Distance, shared by every module that asks "what is nearest".

Lives here rather than in the fuel module because fuel was simply the first
caller. Grocery, dining and apparel ask the same question of the same table,
and a second copy of the haversine formula is how two screens end up quietly
disagreeing about how far away something is.
"""

from __future__ import annotations

import math
from decimal import Decimal

EARTH_RADIUS_KM = 6371.0088

# Straight-line distance understates driving distance - roads bend, rivers need
# bridges, and somewhere across a divided highway needs a U-turn. 1.35 is the
# usual urban allowance and keeps estimates conservative, which is the right
# direction to be wrong in.
ROAD_DISTANCE_FACTOR = Decimal("1.35")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = p2 - p1
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def road_km(lat1: float, lon1: float, lat2: float, lon2: float) -> Decimal:
    """Straight-line distance with the road allowance applied, to 2dp."""
    straight = haversine_km(lat1, lon1, lat2, lon2)
    return (Decimal(str(straight)) * ROAD_DISTANCE_FACTOR).quantize(Decimal("0.01"))


def bounding_box(lat: float, lng: float, radius_km: float):
    """A box that comfortably contains a radius, for pre-filtering in SQL.

    Distance itself is computed in Python, but doing that over every place in
    the country would be absurd. The box narrows it to a few hundred rows
    first; being slightly generous is fine because the exact distance filter
    runs afterwards.

    One degree of latitude is ~111km everywhere. Longitude shrinks towards the
    poles, so it is divided by cos(latitude) - at 14.6 degrees N that is a ~3%
    widening, small but wrong to ignore.
    """
    lat_delta = radius_km / 111.0
    shrink = max(math.cos(math.radians(lat)), 0.01)
    lng_delta = radius_km / (111.0 * shrink)
    return (lat - lat_delta, lng - lng_delta, lat + lat_delta, lng + lng_delta)
