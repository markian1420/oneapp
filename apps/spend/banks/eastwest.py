"""
EastWest.

Promo cards on the listing carry a title, a plain date range, an excerpt and a
row of category pills - everything needed, without opening a single detail page.

The listing is paged, and the pager is a plain ?page=N query, so the reader
walks it until a page repeats what the previous one showed.
"""

from __future__ import annotations

from urllib.parse import urljoin

from .base import (
    BankPromo,
    category_for,
    get,
    merchant_from,
    offer_from,
    promo_dates,
    soup,
    text_of,
)

# EastWest tags a promo with region pills (Luzon, Visayas) alongside the useful
# ones. Those say where, not what, so they are ignored when picking a category.
REGION_PILLS = {
    "luzon", "visayas", "mindanao", "metro manila", "nationwide", "international",
}

# Nine to a page, and the pager counts from zero. The cap is a backstop against
# a site that answers 200 for every page number rather than an expected depth.
MAX_PAGES = 20


def parse(html: str, *, base_url: str) -> list[BankPromo]:
    page = soup(html)
    promos = []

    for card in page.select(".promo-card"):
        heading = card.select_one("h3")
        link = card.select_one("a[href]")
        if not heading or not link:
            continue

        title = text_of(heading)
        href = link["href"]
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if not title or not slug:
            continue

        content = card.select_one(".card-content")
        detail = text_of(card.select_one(".excerpt"))

        # The bare <p> between the heading and the excerpt is the date range.
        period = ""
        if content:
            for para in content.find_all("p", recursive=False):
                period = text_of(para)
                break

        labels = [
            text_of(pill) for pill in card.select(".pill-category")
            if text_of(pill).lower() not in REGION_PILLS
        ]

        brand = merchant_from(title) or merchant_from(detail)
        discount, price = offer_from(f"{title} {detail}")
        # The excerpt is the fallback: "Book until August 14, 2027 for stays
        # until August 31, 2027" is the only date some promos state.
        starts_on, ends_on = promo_dates(period, detail)

        promos.append(BankPromo(
            reference=slug[:120],
            title=title[:160],
            detail=detail[:250],
            brand=brand[:60],
            category=category_for(labels, brand),
            card_name="",
            discount_pct=discount,
            price=price,
            starts_on=starts_on,
            ends_on=ends_on,
            source_url=urljoin(base_url, href),
        ))
    return promos


def collect(source, *, fetch_page=get) -> list[BankPromo]:
    """Walk the pager until a page adds nothing new."""
    promos: list[BankPromo] = []
    seen: set[str] = set()

    for number in range(MAX_PAGES):
        try:
            html = fetch_page(f"{source['url']}?page={number}")
        except Exception:
            break

        fresh = [p for p in parse(html, base_url=source["base"])
                 if p.reference not in seen]
        if not fresh:
            break
        seen.update(p.reference for p in fresh)
        promos.extend(fresh)

    return promos
