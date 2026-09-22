"""
BPI.

The listing does the work for us: every promo card carries its own "Valid
until" line, the title, a one-line description and a link to the detail page.
Nothing needs fetching twice.

The tags say "Credit cards" / "Debit cards", which is the card *type* rather
than a named card, so the qualifying card is left blank - the app reads that as
"any BPI card", which is what the tag actually means.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from .base import (
    MAX_BRAND_WORDS,
    BankPromo,
    category_for,
    merchant_from,
    offer_from,
    promo_dates,
    soup,
    text_of,
)

# BPI names a lot of promos after the merchant and nothing else: "Acacia Hotel
# Davao Promo", "Hotel Adelina Promo". Where the title is only that, the
# merchant is the title.
NAMED_PROMO = re.compile(r"^(.{3,60}?)\s+Promo$", re.I)


def _brand_from(title: str, detail: str) -> str:
    named = NAMED_PROMO.match(title)
    if named and len(named.group(1).split()) <= MAX_BRAND_WORDS:
        return named.group(1)
    return merchant_from(title) or merchant_from(detail)


def parse(html: str, *, base_url: str) -> list[BankPromo]:
    page = soup(html)
    promos = []
    seen = set()

    for card in page.select(".article-page-cont"):
        title = text_of(card.select_one(".tab-head-cont"))
        link = card.select_one(".view-article-link a[href]")
        if not title or not link:
            continue

        href = link["href"]
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if not slug or slug in seen:
            # The page renders the same promos twice, once per view mode.
            continue
        seen.add(slug)

        detail = text_of(card.select_one(".article-desc"))
        brand = _brand_from(title, detail)
        discount, price = offer_from(f"{title} {detail}")
        starts_on, ends_on = promo_dates(text_of(card.select_one(".tab-date-cont")))

        promos.append(BankPromo(
            reference=slug[:120],
            title=title[:160],
            detail=detail[:250],
            brand=brand[:60],
            category=category_for([], brand),
            card_name="",
            discount_pct=discount,
            price=price,
            starts_on=starts_on,
            ends_on=ends_on,
            source_url=urljoin(base_url, href),
        ))
    return promos
