"""
Turning a price series into something worth reading.

The DA publishes a number per commodity per day. On its own that is not useful -
"pork belly is 381.89" answers nothing. What matters is the direction and the
size of the move, which is the difference between a price and its own past.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Max

from .models import CommodityPrice

# The windows worth reporting. A week catches the current swing; a month is
# long enough to separate a real trend from a wet weekend.
SHORT_WINDOW = 7
LONG_WINDOW = 30


@dataclass
class Movement:
    """What one commodity's price has been doing."""

    latest: Decimal | None = None
    latest_on: date | None = None
    previous: Decimal | None = None
    week_change: Decimal | None = None
    month_change: Decimal | None = None
    low: Decimal | None = None
    high: Decimal | None = None
    points: int = 0

    @property
    def week_pct(self) -> Decimal | None:
        return self._pct(self.week_change, SHORT_WINDOW)

    @property
    def month_pct(self) -> Decimal | None:
        return self._pct(self.month_change, LONG_WINDOW)

    def _pct(self, change: Decimal | None, window: int) -> Decimal | None:
        if change is None or self.latest is None:
            return None
        base = self.latest - change
        if not base:
            return None
        return (change / base * 100).quantize(Decimal("0.1"))

    @property
    def direction(self) -> str:
        if self.week_change is None or self.week_change == 0:
            return "flat"
        return "up" if self.week_change > 0 else "down"

    @property
    def badge_class(self) -> str:
        # Up is bad news for a shopper, so the semantics are inverted against
        # the usual finance convention on purpose: red means it costs more.
        return {"up": "badge-danger", "down": "badge-success",
                "flat": "badge-muted"}[self.direction]


def movements(commodities, *, region: str = "NCR",
              as_of: date | None = None) -> dict[int, Movement]:
    """Summarise the recent series for every commodity in one pass.

    One query for the window, then the arithmetic in Python. Doing this per
    commodity would be a query per table row, and the list runs to 163.
    """
    commodities = list(commodities)
    if not commodities:
        return {}

    ids = [c.pk for c in commodities]
    as_of = as_of or (
        CommodityPrice.objects.filter(region=region).aggregate(
            latest=Max("observed_on")
        )["latest"]
    )
    if as_of is None:
        return {c.pk: Movement() for c in commodities}

    # A couple of days of slack past the long window: the DA does not publish
    # at weekends, so "30 days ago" often has no reading of its own and the
    # nearest earlier one has to stand in.
    earliest = as_of - timedelta(days=LONG_WINDOW + 5)

    series: dict[int, list[tuple[date, Decimal]]] = {pk: [] for pk in ids}
    rows = (
        CommodityPrice.objects.filter(
            commodity_id__in=ids, region=region,
            observed_on__gte=earliest, observed_on__lte=as_of,
        )
        .order_by("observed_on")
        .values_list("commodity_id", "observed_on", "price")
    )
    for commodity_id, observed_on, price in rows:
        series[commodity_id].append((observed_on, price))

    resolved: dict[int, Movement] = {}
    for commodity in commodities:
        points = series.get(commodity.pk) or []
        if not points:
            resolved[commodity.pk] = Movement()
            continue

        latest_on, latest = points[-1]
        prices = [price for _, price in points]

        resolved[commodity.pk] = Movement(
            latest=latest,
            latest_on=latest_on,
            previous=points[-2][1] if len(points) > 1 else None,
            week_change=_change(points, latest, latest_on, SHORT_WINDOW),
            month_change=_change(points, latest, latest_on, LONG_WINDOW),
            low=min(prices),
            high=max(prices),
            points=len(points),
        )
    return resolved


def _change(points, latest: Decimal, latest_on: date, window: int) -> Decimal | None:
    """Latest price minus the closest reading on or before the window start.

    Falls back to the earliest reading available rather than returning nothing,
    but only when there is genuinely something older to compare against - a
    two-day-old series must not claim to know a month's movement.
    """
    cutoff = latest_on - timedelta(days=window)
    earlier = [price for observed_on, price in points if observed_on <= cutoff]
    if not earlier:
        return None
    return latest - earlier[-1]


def biggest_movers(*, region: str = "NCR", limit: int = 6,
                   window: int = SHORT_WINDOW) -> list[dict]:
    """The commodities that moved most, in percentage terms, both ways.

    Percentage rather than pesos: a 20-peso move on beef is noise, the same
    move on pechay is the story.
    """
    from .models import Commodity

    commodities = list(
        Commodity.objects.filter(prices__region=region).distinct()
    )
    changes = movements(commodities, region=region)

    scored = []
    for commodity in commodities:
        movement = changes.get(commodity.pk)
        if not movement or movement.points < 3:
            continue
        pct = movement.week_pct if window == SHORT_WINDOW else movement.month_pct
        if pct is None:
            continue
        scored.append({"commodity": commodity, "movement": movement, "pct": pct})

    scored.sort(key=lambda row: row["pct"])
    if len(scored) <= limit:
        return scored

    # Both tails, because a shopper wants the thing to avoid and the thing to
    # stock up on, not just one end of the list.
    half = limit // 2
    return scored[:half] + scored[-half:]
