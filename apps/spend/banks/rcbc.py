"""
RCBC.

The largest listing of the lot - around 150 live merchant offers - but the only
one that keeps its dates on the detail pages rather than the listing. So this
reader works in two passes: read the listing once, then open each promo.

That is ~150 requests, which is why they go out one at a time with a pause
between. A weekly import has no reason to hurry, and hammering someone's site
for a personal budgeting app would be rude.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin

from .base import (
    BankPromo,
    category_for,
    clean,
    get,
    merchant_from,
    offer_from,
    promo_dates,
    soup,
    text_of,
)

# Courtesy pause between detail pages.
DETAIL_DELAY_SECONDS = 0.4

# The date sits in a div the site currently ships commented out, so it is read
# from the raw HTML rather than the parsed tree - a comment has no elements.
DATE_HOLDER = re.compile(r'<div class="datehldr">(.*?)</div>', re.S)

# The mechanics restate it in prose, which survives a template change.
PERIOD_SENTENCE = re.compile(
    r"promo period is from(.{0,80})", re.I | re.S
)


def parse_listing(html: str, *, base_url: str) -> list[BankPromo]:
    """Every promo on the listing, without dates - those need a second pass."""
    page = soup(html)
    promos = []
    seen = set()

    for block in page.select(".promolistdv"):
        link = block.select_one(".title a[href]")
        if not link:
            continue

        href = link["href"]
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        title = text_of(link)
        if not title or not slug or slug in seen:
            continue
        seen.add(slug)

        detail = text_of(block.select_one(".desc"))
        brand = merchant_from(title) or merchant_from(detail)
        discount, price = offer_from(f"{title} {detail}")

        promos.append(BankPromo(
            reference=slug[:120],
            title=title[:160],
            detail=detail[:250],
            brand=brand[:60],
            category=category_for([], brand),
            card_name="",
            discount_pct=discount,
            price=price,
            starts_on=None,
            ends_on=None,
            source_url=urljoin(base_url, href),
        ))
    return promos


def parse_detail(html: str):
    """The promo period a detail page states."""
    holder = DATE_HOLDER.search(html)
    sentence = PERIOD_SENTENCE.search(html)
    return promo_dates(
        clean(holder.group(1)) if holder else "",
        clean(re.sub(r"<[^>]+>", " ", sentence.group(1))) if sentence else "",
    )


def collect(source, *, fetch_page=get, delay: float = DETAIL_DELAY_SECONDS,
            limit: int | None = None) -> list[BankPromo]:
    promos = parse_listing(fetch_page(source["url"]), base_url=source["base"])
    if limit:
        promos = promos[:limit]

    for index, promo in enumerate(promos):
        if index and delay:
            time.sleep(delay)
        try:
            promo.starts_on, promo.ends_on = parse_detail(fetch_page(promo.source_url))
        except Exception:
            # One unreachable detail page should not lose the other 149. The
            # promo simply arrives without an end date, and the importer drops
            # it rather than showing it as running for ever.
            continue
    return promos
