"""
Working out where you are, and what the app therefore knows.

Almost everything in this app is regional. The DOE publishes fuel advisories per
region, the DA publishes its commodity index for NCR only, and places are
imported one province at a time. So an app calibrated for Metro Manila is
quietly wrong the moment you drive home to the province: the map is empty, the
fuel baseline belongs to the wrong region, and the grocery benchmark is for wet
markets 400km away.

Rather than let that happen silently, this resolves the region you are actually
in and reports what does and does not apply there.

Reverse geocoding goes through Nominatim, which is free, is the OSM project's
own service, and asks for no more than one request a second. Results are cached
against a rounded coordinate so a day of moving around a city costs one lookup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
from django.utils import timezone

from .models import GeoCache
from .regions import NCR, REGION_NAMES

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
REQUEST_TIMEOUT = 20

# Two decimal places is about 1.1km - far finer than a region boundary needs,
# and coarse enough that moving around a city reuses one cached answer.
CACHE_PRECISION = 2

# What Nominatim calls each region, mapped to the codes the DOE and DA use.
# Its "region" field carries the administrative region; "state" carries the
# province, which is what the place importer wants.
NOMINATIM_REGIONS = {
    "metro manila": NCR,
    "national capital region": NCR,
    "ncr": NCR,
    "cordillera administrative region": "CAR",
    "cordillera": "CAR",
    "ilocos region": "I",
    "cagayan valley": "II",
    "central luzon": "III",
    "calabarzon": "IV-A",
    "mimaropa": "IV-B",
    "southwestern tagalog region": "IV-B",
    "bicol region": "V",
    "western visayas": "VI",
    "central visayas": "VII",
    "eastern visayas": "VIII",
    "zamboanga peninsula": "IX",
    "northern mindanao": "X",
    "davao region": "XI",
    "soccsksargen": "XII",
    "caraga": "XIII",
    "bangsamoro autonomous region in muslim mindanao": "BARMM",
    "bangsamoro": "BARMM",
}


@dataclass
class Fix:
    """Where the app thinks you are."""

    latitude: float
    longitude: float
    region: str = ""
    region_name: str = ""
    province: str = ""
    city: str = ""
    resolved: bool = False
    error: str = ""

    @property
    def label(self) -> str:
        parts = [p for p in (self.city, self.province) if p]
        if not parts and self.region_name:
            return self.region_name
        return ", ".join(parts) or "an unknown place"


def _round(value: float) -> float:
    return round(float(value), CACHE_PRECISION)


def _region_code(address: dict) -> tuple[str, str]:
    """Map Nominatim's naming onto the DOE/DA region codes."""
    raw = (address.get("region") or address.get("state") or "").strip()
    code = NOMINATIM_REGIONS.get(raw.lower(), "")
    if code:
        return code, REGION_NAMES.get(code, raw)

    # Some provinces come back with no region at all. The province name is
    # still enough, because the app already maps every province to its region.
    from .regions import PROVINCE_TO_REGION

    province = (address.get("state") or "").strip().lower()
    code = PROVINCE_TO_REGION.get(province, "")
    return code, REGION_NAMES.get(code, raw) if code else (code, raw)


def reverse_geocode(latitude: float, longitude: float, *, use_cache: bool = True) -> Fix:
    """Resolve a coordinate to a Philippine region and province.

    Failure is returned rather than raised. A location the app cannot resolve
    should degrade to "we do not know where you are", not to a stack trace on
    a screen you opened while standing in a car park.
    """
    fix = Fix(latitude=_round(latitude), longitude=_round(longitude))

    if use_cache:
        cached = GeoCache.objects.filter(
            latitude=fix.latitude, longitude=fix.longitude
        ).first()
        if cached:
            return Fix(
                latitude=fix.latitude, longitude=fix.longitude,
                region=cached.region, region_name=cached.region_name,
                province=cached.province, city=cached.city, resolved=True,
            )

    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={
                "format": "jsonv2", "lat": latitude, "lon": longitude,
                "zoom": 10, "addressdetails": 1,
            },
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "OneApp/1.0 (personal budgeting app)"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Reverse geocode failed: %s", exc)
        fix.error = "Could not work out where that is."
        return fix

    address = payload.get("address", {})
    if address.get("country_code", "").lower() != "ph":
        fix.error = "That location is outside the Philippines."
        return fix

    code, name = _region_code(address)
    fix.region = code
    fix.region_name = name
    fix.province = (address.get("state") or "").strip()
    fix.city = (
        address.get("city") or address.get("town")
        or address.get("municipality") or ""
    ).strip()
    fix.resolved = bool(code)

    if fix.resolved and use_cache:
        GeoCache.objects.update_or_create(
            latitude=fix.latitude, longitude=fix.longitude,
            defaults={
                "region": fix.region, "region_name": fix.region_name,
                "province": fix.province, "city": fix.city,
                "looked_up_at": timezone.now(),
            },
        )
    return fix
