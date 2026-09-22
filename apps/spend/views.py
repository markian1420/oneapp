"""Shopping screens: nearby places and public card promos."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.core.categories import SpendCategory
from apps.core.tables import Column, build_list_table
from apps.core.views import module

from .shopping import CATEGORY_KINDS, reference_price, where_to_buy
from .services import issuers, live_promos


def _promo_columns(*, bank: bool) -> list[Column]:
    """Columns for a promo table. The bank column only earns its width when
    more than one issuer is on screen."""
    columns = [Column("title", "Offer", order_by=("title",))]
    if bank:
        columns.append(Column("issuer", "Bank", order_by=("issuer",)))
    columns += [
        Column("brand", "Where", order_by=("brand",)),
        Column("card_name", "Qualifying card", order_by=("card_name",)),
        Column("discount", "Deal", order_by=("discount_pct",), align="right"),
        Column("ends_on", "Ends", order_by=("ends_on",)),
    ]
    return columns


def _promo_rows(promos) -> list[dict]:
    return [
        {
            "promo": p, "title": p.title, "issuer": p.issuer, "brand": p.brand,
            "card_name": p.card_name,
            "discount": p.discount_pct or p.price or 0,
            "ends_on": p.ends_on,
        }
        for p in promos
    ]


@login_required
@module("spend_card_promos", "Card promos")
def card_promos(request):
    """Every card promo on record, from every issuer.

    A directory of what banks are running, not a wallet. The app stores no
    card of yours - only the offers, which are public facts about the banks.
    """
    issuer = request.GET.get("issuer", "").strip()
    category = request.GET.get("category", "")
    if category not in SpendCategory.values:
        category = ""
    search = request.GET.get("q", "").strip()

    live = live_promos(category=category, issuer=issuer, card_promos=True,
                       search=search)

    # Paginated like every other list in the app. Six banks publish over 900
    # live promos between them, and rendering them all was the exact thing
    # server-side paging exists to avoid.
    table = build_list_table(
        request, _promo_rows(live), _promo_columns(bank=True),
        default_sort="ends_on", preserve=("issuer", "category", "q"),
    )

    return render(request, "spend/card_promos.html", {
        "table": table,
        "rows": table.page.object_list,
        "total": len(live),
        "issuers": issuers(),
        "issuer": issuer,
        "categories": SpendCategory.choices,
        "category": category,
        "search": search,
        "expiring": [p for p in live if p.days_left is not None and p.days_left <= 7],
        "undated": [p for p in live if p.undated],
    })


@login_required
@module("spend_where", "Where to buy")
def where(request):
    """Nearest places that sell what you are after, and what is known there."""
    category = request.GET.get("category", "")
    if category not in CATEGORY_KINDS:
        category = SpendCategory.GROCERY

    item = request.GET.get("q", "").strip()

    origin = None
    try:
        origin = (float(request.GET["lat"]), float(request.GET["lng"]))
    except (KeyError, ValueError):
        pass

    options = where_to_buy(origin=origin, category=category, item=item) if origin else []

    return render(request, "spend/where.html", {
        "options": options,
        "origin": origin,
        "category": category,
        "categories": [
            (value, label) for value, label in SpendCategory.choices
            if value in CATEGORY_KINDS
        ],
        "item": item,
        "reference": reference_price(item) if item else None,
        "priced": [o for o in options if o.has_price],
        "with_promos": [o for o in options if o.promos],
    })
