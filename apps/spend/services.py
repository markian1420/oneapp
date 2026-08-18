"""
Spend summaries, wardrobe economics, and which promos are actually live.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Sum
from django.utils import timezone

from apps.core.categories import SpendCategory

from .models import Promo, Purchase, PurchaseItem


def month_bounds(when: date | None = None) -> tuple[date, date]:
    when = when or timezone.localdate()
    start = when.replace(day=1)
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    return start, nxt - timedelta(days=1)


@dataclass
class CategoryTotal:
    category: str
    label: str
    spent: Decimal
    count: int
    previous: Decimal

    @property
    def change(self) -> Decimal:
        return self.spent - self.previous

    @property
    def change_pct(self) -> Decimal | None:
        if not self.previous:
            return None
        return (self.change / self.previous * 100).quantize(Decimal("0.1"))


def spend_by_category(when: date | None = None) -> list[CategoryTotal]:
    """This month against last, per category.

    Fuel is folded in from FillUp rather than left out: a spend summary that
    silently omits the biggest recurring cost is worse than no summary.
    """
    from apps.fuel.models import FillUp

    start, end = month_bounds(when)
    previous_end = start - timedelta(days=1)
    previous_start, _ = month_bounds(previous_end)

    def totals(from_date, to_date) -> dict[str, tuple[Decimal, int]]:
        rows = (
            Purchase.objects.filter(
                occurred_at__date__gte=from_date, occurred_at__date__lte=to_date
            )
            .values("category")
            .annotate(spent=Sum("total"), n=Count("id"))
        )
        out = {r["category"]: (r["spent"] or Decimal("0"), r["n"]) for r in rows}

        fuel = FillUp.objects.filter(
            filled_at__date__gte=from_date, filled_at__date__lte=to_date
        ).aggregate(spent=Sum("total_cost"), n=Count("id"))
        if fuel["n"]:
            spent, count = out.get(SpendCategory.FUEL, (Decimal("0"), 0))
            out[SpendCategory.FUEL] = (spent + (fuel["spent"] or Decimal("0")),
                                       count + fuel["n"])
        return out

    now, before = totals(start, end), totals(previous_start, previous_end)

    result = []
    for category in SpendCategory:
        spent, count = now.get(category.value, (Decimal("0"), 0))
        result.append(CategoryTotal(
            category=category.value,
            label=category.label,
            spent=spent,
            count=count,
            previous=before.get(category.value, (Decimal("0"), 0))[0],
        ))
    result.sort(key=lambda c: c.spent, reverse=True)
    return result


def live_promos(category: str = "", brand: str = "", *,
                issuer: str = "", card_promos: bool | None = None) -> list[Promo]:
    """Promos running today, soonest to expire first.

    Filtered in Python rather than SQL because "live" depends on two nullable
    dates and the readable version of that query is the model property.
    """
    queryset = Promo.objects.all()
    if category:
        queryset = queryset.filter(category=category)
    if brand:
        queryset = queryset.filter(brand__iexact=brand)
    if issuer:
        queryset = queryset.filter(issuer__iexact=issuer)
    if card_promos is True:
        queryset = queryset.exclude(issuer="")
    elif card_promos is False:
        queryset = queryset.filter(issuer="")

    promos = [p for p in queryset if p.is_live]
    # Undated promos sort last: they are the ones most likely to have quietly
    # ended, so they should not lead the list.
    promos.sort(key=lambda p: (p.ends_on is None, p.ends_on or date.max))
    return promos


def expiring_promos(within_days: int = 3) -> list[Promo]:
    return [
        p for p in live_promos()
        if p.days_left is not None and p.days_left <= within_days
    ]


def stale_promos(older_than_days: int = 60) -> list[Promo]:
    """Undated promos entered long ago.

    Not expired - nobody knows - but old enough that trusting them is a
    gamble. Prompting a review is more honest than showing them as live for
    ever.
    """
    cutoff = timezone.now() - timedelta(days=older_than_days)
    return [
        p for p in Promo.objects.filter(ends_on__isnull=True, added_at__lt=cutoff)
    ]


def wardrobe(limit: int | None = None) -> list[PurchaseItem]:
    """Clothing, worst cost-per-wear first.

    Unworn items lead, because they are the ones the number is trying to make
    you notice.
    """
    items = list(
        PurchaseItem.objects.filter(is_wearable=True)
        .select_related("purchase", "purchase__place")
    )
    # Unworn first, then the worn ones by descending cost per wear. Ascending
    # sort with a negated secondary key, because reverse=True would also flip
    # the primary and put the worn items on top.
    items.sort(key=lambda i: (
        0 if i.wears == 0 else 1,
        -(i.cost_per_wear or Decimal("0")),
    ))
    return items[:limit] if limit else items


def wardrobe_summary() -> dict:
    items = list(PurchaseItem.objects.filter(is_wearable=True))
    worn = [i for i in items if i.wears]

    spent = sum((i.amount for i in items), Decimal("0"))
    wears = sum(i.wears for i in items)

    return {
        "items": len(items),
        "unworn": len(items) - len(worn),
        "spent": spent,
        "wears": wears,
        "cost_per_wear": (
            (spent / wears).quantize(Decimal("0.01")) if wears else None
        ),
    }


def basket_comparison(description: str) -> list[dict]:
    """What one item has cost at each shop you bought it from.

    The supermarket equivalent of the fuel price map, built entirely from
    receipts because no Philippine supermarket publishes shelf prices.
    """
    rows = (
        PurchaseItem.objects.filter(description__iexact=description)
        .select_related("purchase", "purchase__place")
        .values("purchase__place__id", "purchase__place__name",
                "purchase__place__brand", "purchase__merchant")
        .annotate(
            average=Avg("unit_price"), times=Count("id"),
        )
        .order_by("average")
    )

    return [
        {
            "place_id": r["purchase__place__id"],
            "where": (
                r["purchase__place__brand"]
                or r["purchase__place__name"]
                or r["purchase__merchant"]
                or "Unrecorded"
            ),
            "average": r["average"],
            "times": r["times"],
        }
        for r in rows if r["average"] is not None
    ]


def card_promos_at(place) -> list[Promo]:
    """Card promos usable at one place.

    Matches on brand first, then falls back to the category, because a bank
    deal is usually written against a chain but sometimes against a whole
    category ("5% on all dining").
    """
    from apps.core.categories import category_for_place

    category = category_for_place(place.kind)
    by_brand = live_promos(brand=place.brand, card_promos=True) if place.brand else []
    by_category = [
        p for p in live_promos(category=category, card_promos=True)
        if not p.brand
    ]
    return by_brand + by_category


def issuers() -> list[str]:
    """Every issuer named in the promo list, for the filter."""
    return sorted(
        {p.issuer for p in Promo.objects.exclude(issuer="") if p.issuer}
    )
