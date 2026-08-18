"""
Price resolution and refuelling arithmetic.

Two jobs live here. The first is deciding what price to show for a station,
given that the good source (your own receipt) covers a handful of stations and
the broad source (the DOE weekly advisory) covers a brand across a whole region.
The second is turning a price gap into pesos, because a two-peso saving on a
tank is not a two-peso saving once the detour to reach it is paid for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from .models import DOEAdvisory, PriceObservation, PriceTier, Station

# Straight-line distance understates driving distance - roads bend, rivers need
# bridges, and a station across a divided highway needs a U-turn. 1.35 is the
# usual urban planning fudge and keeps the savings estimate conservative, which
# is the right direction to be wrong in: it under-promises the saving.
ROAD_DISTANCE_FACTOR = Decimal("1.35")

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Quote:
    """A price for one station and grade, with its provenance attached."""

    price: Decimal | None
    tier: str
    as_of: date | None = None
    detail: str = ""

    @property
    def is_known(self) -> bool:
        return self.price is not None

    @property
    def tier_label(self) -> str:
        return PriceTier(self.tier).label

    @property
    def badge_class(self) -> str:
        return {
            PriceTier.LOGGED: "badge-success",
            PriceTier.ADVISORY: "badge-info",
            PriceTier.ESTIMATED: "badge-warning",
            PriceTier.UNKNOWN: "badge-muted",
        }[PriceTier(self.tier)]


UNKNOWN_QUOTE = Quote(price=None, tier=PriceTier.UNKNOWN, detail="No price on record")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = p2 - p1
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def quotes_for(stations, fuel_type: str) -> dict[int, Quote]:
    """Resolve a price for every station in one pass.

    Two queries regardless of how many stations are on screen. Doing this per
    marker would be a query per pin, and the map draws up to a few hundred.

    Precedence, best first:

    1. A fresh price you logged at that exact station.
    2. This week's DOE advisory for that brand in that region.
    3. The region's prevailing advisory price, ignoring brand.
    4. A stale price you logged there.

    (3) deliberately outranks (4). Pump prices move every Tuesday, so a
    three-week-old receipt from the right station is usually further off than
    this week's number for the wrong brand - and both are labelled Estimated,
    so neither is being passed off as fact.
    """
    stations = list(stations)
    if not stations:
        return {}

    ids = [s.pk for s in stations]
    regions = {s.region for s in stations if s.region}

    fresh_cutoff = timezone.now() - timedelta(days=settings.PRICE_FRESH_DAYS)

    latest_observation: dict[int, PriceObservation] = {}
    observations = (
        PriceObservation.objects.filter(station_id__in=ids, fuel_type=fuel_type)
        .order_by("station_id", "-observed_at")
        .only("station_id", "price", "observed_at", "source")
    )
    for observation in observations:
        # Ordered newest-first within each station, so the first one wins.
        latest_observation.setdefault(observation.station_id, observation)

    by_brand: dict[tuple[str, str], DOEAdvisory] = {}
    if regions:
        advisories = (
            DOEAdvisory.objects.filter(fuel_type=fuel_type, region__in=regions)
            .order_by("-week_of")
            .only("region", "brand", "price", "week_of")
        )
        for advisory in advisories:
            by_brand.setdefault((advisory.region, advisory.brand), advisory)

    resolved: dict[int, Quote] = {}
    for station in stations:
        observation = latest_observation.get(station.pk)

        if observation and observation.observed_at >= fresh_cutoff:
            resolved[station.pk] = Quote(
                price=observation.price,
                tier=PriceTier.LOGGED,
                as_of=timezone.localtime(observation.observed_at).date(),
                detail=observation.get_source_display(),
            )
            continue

        advisory = by_brand.get((station.region, station.brand))
        if advisory:
            resolved[station.pk] = Quote(
                price=advisory.price,
                tier=PriceTier.ADVISORY,
                as_of=advisory.week_of,
                detail=f"{station.brand} in {station.region}",
            )
            continue

        prevailing = by_brand.get((station.region, ""))
        if prevailing:
            resolved[station.pk] = Quote(
                price=prevailing.price,
                tier=PriceTier.ESTIMATED,
                as_of=prevailing.week_of,
                detail=f"{station.region} prevailing price, any brand",
            )
            continue

        if observation:
            resolved[station.pk] = Quote(
                price=observation.price,
                tier=PriceTier.ESTIMATED,
                as_of=timezone.localtime(observation.observed_at).date(),
                detail="Your last price here, now out of date",
            )
            continue

        resolved[station.pk] = UNKNOWN_QUOTE

    return resolved


@dataclass
class Option:
    """One station scored as a place to refuel."""

    station: Station
    quote: Quote
    distance_km: Decimal | None
    fuel_cost: Decimal | None
    detour_cost: Decimal | None
    effective_cost: Decimal | None
    saving_vs_worst: Decimal | None = None

    @property
    def is_comparable(self) -> bool:
        return self.effective_cost is not None


def _round(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places))


def score_options(
    stations,
    fuel_type: str,
    *,
    liters: Decimal,
    km_per_liter: Decimal,
    origin: tuple[float, float] | None = None,
    quotes: dict[int, Quote] | None = None,
) -> list[Option]:
    """Rank stations by what a tank there really costs.

    The detour is charged both ways and priced at the fuel you would burn
    getting there, which is what makes a cheap station 9km away lose to a
    dearer one you pass anyway. Time and wear are not costed - they are real
    but not knowable from here, so the number stays one you can check.
    """
    stations = list(stations)
    quotes = quotes or quotes_for(stations, fuel_type)

    options: list[Option] = []
    for station in stations:
        quote = quotes.get(station.pk, UNKNOWN_QUOTE)

        distance = None
        if origin is not None:
            straight = haversine_km(
                origin[0], origin[1], float(station.latitude), float(station.longitude)
            )
            distance = _round(Decimal(str(straight)) * ROAD_DISTANCE_FACTOR)

        fuel_cost = detour_cost = effective = None
        if quote.price is not None:
            fuel_cost = _round(quote.price * liters)
            if distance is not None and km_per_liter > 0:
                burned = (distance * 2) / km_per_liter
                detour_cost = _round(burned * quote.price)
                effective = _round(fuel_cost + detour_cost)
            else:
                effective = fuel_cost

        options.append(
            Option(
                station=station,
                quote=quote,
                distance_km=distance,
                fuel_cost=fuel_cost,
                detour_cost=detour_cost,
                effective_cost=effective,
            )
        )

    comparable = [o for o in options if o.is_comparable]
    if comparable:
        worst = max(o.effective_cost for o in comparable)
        for option in comparable:
            option.saving_vs_worst = _round(worst - option.effective_cost)

    # Priceless stations sort last rather than disappearing: you may still want
    # to see that something is there, you just cannot compare it.
    options.sort(
        key=lambda o: (o.effective_cost is None, o.effective_cost or Decimal(0))
    )
    return options


def fuel_economy(fill_ups) -> Decimal | None:
    """Actual km per litre from consecutive full-tank fill-ups.

    Only full-to-full intervals count. A partial fill leaves an unknown amount
    in the tank, so the litres bought no longer match the distance driven and
    the figure would be nonsense.
    """
    ordered = [f for f in sorted(fill_ups, key=lambda f: f.filled_at) if f.odometer_km]

    distance = Decimal(0)
    liters = Decimal(0)
    previous = None
    for fill_up in ordered:
        if previous and previous.is_full_tank and fill_up.is_full_tank:
            gap = fill_up.odometer_km - previous.odometer_km
            if gap > 0:
                distance += Decimal(gap)
                liters += fill_up.liters
        previous = fill_up

    if distance <= 0 or liters <= 0:
        return None
    return _round(distance / liters, "0.01")


def week_start(value: date | datetime | None = None) -> date:
    """Monday of the week a date falls in.

    DOE advisories take effect on Tuesday, but keying them to Monday keeps the
    week boundary somewhere nothing happens, so an import that runs on the day
    of the change cannot land in two different weeks depending on the hour.
    """
    value = value or timezone.localdate()
    if isinstance(value, datetime):
        value = value.date()
    return value - timedelta(days=value.weekday())
