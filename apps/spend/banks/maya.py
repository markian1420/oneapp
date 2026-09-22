"""
Maya.

Not a traditional bank, but it issues a card and runs the sort of everyday
cashback offers this app exists to notice. The deals listing states each promo's
end date on the card itself - "Until December 31, 2026" - which is more than
most of the incumbents manage.
"""

from __future__ import annotations

from urllib.parse import urljoin

from .base import (
    BankPromo,
    category_for,
    merchant_from,
    offer_from,
    promo_dates,
    soup,
    text_of,
)


def parse(html: str, *, base_url: str) -> list[BankPromo]:
    page = soup(html)
    promos = []
    seen = set()

    for link in page.select('a[href*="/deals/"]'):
        href = link["href"]
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if not slug or slug in {"deals", "archive"} or slug in seen:
            continue

        # A promo card carries both a status line and a title; a bare nav link
        # carries neither, which is how the two are told apart.
        status = link.select_one(".promo-ongoing")
        heading = link.select_one("p")
        title = text_of(heading)
        if not title or not status:
            continue
        seen.add(slug)

        card = link.parent
        labels = [text_of(tag) for tag in card.select(".tag")] if card else []

        brand = merchant_from(title)
        discount, price = offer_from(title)
        starts_on, ends_on = promo_dates(text_of(status))

        promos.append(BankPromo(
            reference=slug[:120],
            title=title[:160],
            detail="",
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
