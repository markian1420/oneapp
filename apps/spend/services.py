"""Public promo helpers for the shopping screens."""

from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

from .models import Promo


def live_promos(category: str = "", brand: str = "", *,
                issuer: str = "", card_promos: bool | None = None,
                search: str = "") -> list[Promo]:
    """Promos running today, soonest to expire first.

    Filtered in Python rather than SQL because "live" depends on two nullable
    dates and the readable version of that query is the model property.
    Everything else narrows in the database first, so "chicken" does not drag
    a thousand rows into Python to throw nine hundred away.
    """
    queryset = Promo.objects.all()
    if search:
        queryset = queryset.filter(
            Q(title__icontains=search)
            | Q(brand__icontains=search)
            | Q(detail__icontains=search)
            | Q(card_name__icontains=search)
            | Q(issuer__icontains=search)
        )
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


def expiring_promos(within_days: int = 3, *,
                    card_promos: bool | None = None) -> list[Promo]:
    return [
        p for p in live_promos(card_promos=card_promos)
        if p.days_left is not None and p.days_left <= within_days
    ]


def stale_promos(older_than_days: int = 60, *,
                 card_promos: bool | None = None) -> list[Promo]:
    """Undated promos first seen long ago.

    Not expired - nobody knows - but old enough that trusting them is a
    gamble. Prompting a review is more honest than showing them as live for
    ever.
    """
    cutoff = timezone.now() - timedelta(days=older_than_days)
    queryset = Promo.objects.filter(ends_on__isnull=True, added_at__lt=cutoff)
    if card_promos is True:
        queryset = queryset.exclude(issuer="")
    elif card_promos is False:
        queryset = queryset.filter(issuer="")
    return list(queryset)


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
