"""
Where to buy something, near where you are standing.

The same shape as the fuel comparison - nearest first, priced, ranked - but the
price side is far weaker and the screen has to say so. Fuel has the DOE weekly
advisory to fall back on, so every station can carry a number. No Philippine
supermarket publishes shelf prices at all, so the only per-store grocery price
that will ever exist here is one off your own receipt.

What the app can offer instead, honestly:

  * distance, which it knows exactly;
  * live card promos at that place, which the banks publish and which are real;
  * the DA regional price as a benchmark for what a fair ask looks like;
  * your own logged price, once there is one.

The last of those is the only one that is genuinely per-store, and it is the
one that starts empty. Saying that plainly is better than ranking shops by a
number that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Avg, Count, Max

from apps.core.categories import SpendCategory
from apps.places.geo import bounding_box, road_km
from apps.places.models import Place, PlaceKind

from .models import PurchaseItem
from .services import live_promos

# Which kinds of place sell which category. A category the app has no places
# for would render an empty screen with no explanation.
CATEGORY_KINDS = {
    SpendCategory.GROCERY: [
        PlaceKind.SUPERMARKET, PlaceKind.MARKET, PlaceKind.CONVENIENCE,
    ],
    SpendCategory.DINING: [PlaceKind.FAST_FOOD, PlaceKind.RESTAURANT],
    SpendCategory.APPAREL: [PlaceKind.CLOTHES, PlaceKind.MALL],
    SpendCategory.HEALTH: [PlaceKind.PHARMACY],
    SpendCategory.FUEL: [PlaceKind.FUEL],
}

SEARCH_RADIUS_KM = 8.0
MAX_CANDIDATES = 1200

# Somewhere you would do a shop, ahead of somewhere you would top up. Metro
# Manila has 3,670 convenience stores against 733 supermarkets, so by distance
# alone the answer to "where do I buy chicken" is nine 7-Elevens - technically
# nearest, useless as advice.
KIND_PRIORITY = {
    PlaceKind.SUPERMARKET: 0,
    PlaceKind.MARKET: 0,
    PlaceKind.MALL: 1,
    # A named shop beats the mall containing it: a promo says "at Uniqlo",
    # not "at the mall Uniqlo is in".
    PlaceKind.CLOTHES: 0,
    PlaceKind.RESTAURANT: 0,
    PlaceKind.FAST_FOOD: 0,
    PlaceKind.PHARMACY: 0,
    PlaceKind.FUEL: 0,
    PlaceKind.CONVENIENCE: 1,
}


@dataclass
class Option:
    """One place, with everything known about buying there."""

    place: Place
    distance_km: Decimal
    your_price: Decimal | None = None
    your_visits: int = 0
    # Promos naming this brand specifically. These are the ones worth crossing
    # a road for.
    promos: list = field(default_factory=list)
    # Category-wide offers that apply here and at every other place of the same
    # kind. Kept apart because listing them per row made every mall look
    # identical - "23 promos" against all of them said nothing at all.
    general_promos: list = field(default_factory=list)

    @property
    def has_price(self) -> bool:
        return self.your_price is not None

    @property
    def best_discount(self) -> Decimal | None:
        discounts = [p.discount_pct for p in self.promos if p.discount_pct]
        return max(discounts) if discounts else None

    @property
    def is_convenience(self) -> bool:
        return self.place.kind == PlaceKind.CONVENIENCE

    @property
    def why(self) -> str:
        """One line on what is actually known about this place."""
        if self.has_price:
            return f"You paid {self.your_price:.2f} here across {self.your_visits} visit(s)"
        if self.promos:
            return f"{len(self.promos)} promo(s) naming this brand"
        return "Nothing known yet - no price logged, no promo on record"


def known_prices(description: str) -> dict[int, tuple[Decimal, int]]:
    """What you have paid for one item, per place.

    The only per-store price data that exists. Averaged across visits rather
    than taking the latest, because a single receipt is as likely to catch a
    promotion as a normal price.
    """
    if not description.strip():
        return {}

    rows = (
        PurchaseItem.objects.filter(
            description__icontains=description.strip(),
            purchase__place__isnull=False,
            unit_price__isnull=False,
        )
        .values("purchase__place_id")
        .annotate(average=Avg("unit_price"), times=Count("id"))
    )
    return {
        r["purchase__place_id"]: (
            r["average"].quantize(Decimal("0.01")), r["times"]
        )
        for r in rows
    }


def promos_by_place(places, category: str) -> tuple[dict[int, list], list]:
    """Promos naming each brand, and the category-wide ones separately.

    Merging the two made every mall show the same count and look identical.
    A promo that names Uniqlo is a reason to go to that mall; one that applies
    to all clothing is context, and belongs beside the list rather than in
    every row of it.
    """
    running = live_promos(category=category, card_promos=True)
    by_brand: dict[str, list] = {}
    category_wide = []

    for promo in running:
        if promo.brand:
            by_brand.setdefault(promo.brand.lower(), []).append(promo)
        else:
            category_wide.append(promo)

    per_place = {
        place.pk: by_brand.get((place.brand or "").lower(), [])
        for place in places
    }
    return per_place, category_wide


def reference_price(description: str) -> dict | None:
    """The DA's regional price for an item, when it tracks one.

    A benchmark, not a shelf price: it says what the going rate is across NCR,
    which is what you need to judge whether a stall is asking fairly. It will
    not match a supermarket receipt and is not meant to.
    """
    from apps.grocery.models import Commodity

    if not description.strip():
        return None

    commodity = (
        Commodity.objects.filter(name__icontains=description.strip())
        .annotate(latest=Max("prices__observed_on"))
        .exclude(latest=None)
        .order_by("name")
        .first()
    )
    if not commodity:
        return None

    price = commodity.prices.order_by("-observed_on").first()
    if not price:
        return None

    return {
        "commodity": commodity,
        "price": price.price,
        "observed_on": price.observed_on,
        "unit": commodity.unit,
    }


def where_to_buy(
    *,
    origin: tuple[float, float],
    category: str,
    item: str = "",
    limit: int = 25,
) -> list[Option]:
    """Nearby places that sell this, nearest first.

    Deliberately ordered by distance rather than by price. Ranking by price
    would imply the app knows what each shop charges, and for anything but
    fuel it does not - so it leads with the thing it does know and shows the
    price gaps as gaps.
    """
    kinds = CATEGORY_KINDS.get(category, [])
    if not kinds:
        return []

    south, west, north, east = bounding_box(origin[0], origin[1], SEARCH_RADIUS_KM)
    candidates = list(
        Place.objects.filter(
            kind__in=kinds,
            latitude__gte=south, latitude__lte=north,
            longitude__gte=west, longitude__lte=east,
        )[:MAX_CANDIDATES]
    )
    if not candidates:
        return []

    scored = [
        (road_km(origin[0], origin[1], float(p.latitude), float(p.longitude)), p)
        for p in candidates
    ]
    # Somewhere you would actually do this shop first, then by distance.
    scored.sort(key=lambda row: (
        not row[1].is_favorite,
        KIND_PRIORITY.get(row[1].kind, 0),
        row[0],
    ))
    nearest = scored[:limit]

    places = [p for _, p in nearest]
    prices = known_prices(item)
    per_place, category_wide = promos_by_place(places, category)

    options = []
    for distance, place in nearest:
        price, visits = prices.get(place.pk, (None, 0))
        options.append(Option(
            place=place,
            distance_km=distance,
            your_price=price,
            your_visits=visits,
            promos=per_place.get(place.pk, []),
            general_promos=category_wide,
        ))

    # Anywhere you have a price, or a promo naming that brand, leads - those
    # are the only rows carrying information beyond "it is close".
    options.sort(key=lambda o: (
        not (o.has_price or o.promos),
        KIND_PRIORITY.get(o.place.kind, 0),
        o.distance_km,
    ))
    return options
