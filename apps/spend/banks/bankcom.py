"""
Bank of Commerce.

A WordPress site, so the listing is a run of <article> elements with the promo
mechanics inlined as an excerpt - and the excerpt reliably opens with
"Promo Period: August 17 to December 18, 2026". Paged, eleven to a page.
"""

from __future__ import annotations

import re
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

# The excerpt says it plainly; the whole excerpt is the fallback.
PERIOD = re.compile(r"promo period\s*[:\-]?\s*(.{0,90})", re.I)

# A listing that never stops paging would loop for ever on a site that answers
# 200 for every page number. Real listings here run to three.
MAX_PAGES = 10


def parse(html: str, *, base_url: str) -> list[BankPromo]:
    page = soup(html)
    promos = []

    for article in page.select("article"):
        link = article.select_one(".entry-title a[href]")
        if not link:
            continue

        href = link["href"]
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        title = text_of(link)
        if not title or not slug:
            continue

        body = text_of(article.select_one(".entry-content"))
        # Drop the "Read more" the theme appends to every excerpt.
        detail = re.sub(r"\s*Read more\s*$", "", body).strip()

        period = PERIOD.search(body)
        starts_on, ends_on = promo_dates(
            period.group(1) if period else "", body
        )

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
            starts_on=starts_on,
            ends_on=ends_on,
            source_url=urljoin(base_url, href),
        ))
    return promos


def collect(source, *, fetch_page=get) -> list[BankPromo]:
    """Walk the pager until a page adds nothing new."""
    promos: list[BankPromo] = []
    seen: set[str] = set()

    for number in range(1, MAX_PAGES + 1):
        url = source["url"] if number == 1 else f"{source['url'].rstrip('/')}/page/{number}/"
        try:
            html = fetch_page(url)
        except Exception:
            break

        fresh = [p for p in parse(html, base_url=source["base"])
                 if p.reference not in seen]
        if not fresh:
            break
        seen.update(p.reference for p in fresh)
        promos.extend(fresh)

    return promos
