"""
Metrobank.

The best structured data in this entire project. The promos page is a Next.js
app whose __NEXT_DATA__ payload carries every promo as a record with a title, a
description, start and expiry timestamps, the qualifying cards and a category.
No scraping of rendered HTML, no OCR of a poster, and - unlike every merchant
source - a real end date on every row.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from .base import (
    BankPromo,
    category_for,
    clean,
    merchant_from,
    offer_from,
)

# The page is ~12MB because the whole promo catalogue ships inside it. Worth
# saying out loud so the long timeout does not look arbitrary.
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# When every card in the issuer's range qualifies, naming them all is noise.
GENERIC_CARD_COUNT = 4


def _parse_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return None


def _cards_from(raw_cards) -> str:
    names = [
        clean(c.get("title") if isinstance(c, dict) else str(c))
        for c in (raw_cards or [])
    ]
    names = [n for n in names if n]
    if not names or len(names) >= GENERIC_CARD_COUNT:
        # Blank means "any card from this issuer", which is what a full list
        # amounts to and is far easier to read.
        return ""
    return ", ".join(names[:3])


def _labels(raw_categories) -> list[str]:
    return [
        entry.get("category", "")
        for entry in (raw_categories or [])
        if isinstance(entry, dict)
    ]


def parse(html: str, *, base_url: str) -> list[BankPromo]:
    """Pull every promo record out of the page payload."""
    match = NEXT_DATA.search(html)
    if not match:
        raise ValueError(
            "No __NEXT_DATA__ payload found - the page structure has changed."
        )

    payload = json.loads(match.group(1))
    try:
        records = payload["props"]["pageProps"]["data"]["promoDetails"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Promo list not where expected in the payload: {exc}") from exc

    promos = []
    for record in records:
        title = clean(record.get("title", ""))
        if not title:
            continue

        brand = merchant_from(title)
        discount, price = offer_from(title)
        slug = record.get("slug") or ""

        promos.append(BankPromo(
            reference=slug or title[:120],
            title=title[:160],
            detail=clean(record.get("description", ""))[:250],
            brand=brand[:60],
            category=category_for(_labels(record.get("categories")), brand),
            card_name=_cards_from(record.get("cards"))[:120],
            discount_pct=discount,
            price=price,
            starts_on=_parse_date(record.get("startDate")),
            ends_on=_parse_date(record.get("expirationDate")),
            source_url=f"{base_url.rstrip('/')}{slug}" if slug else base_url,
        ))
    return promos
