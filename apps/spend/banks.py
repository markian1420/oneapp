"""
Importing bank card promos.

Metrobank turns out to publish the best structured data in this entire project.
Its promos page is a Next.js app whose __NEXT_DATA__ payload carries every
promo as a record with a title, a description, start and expiry timestamps, the
qualifying cards and a category. No scraping of rendered HTML, no OCR of a
poster, and - unlike every merchant promo source - a real end date on every row.

That end date is the whole point. A promo tracker showing expired offers is
worse than no tracker, and this is the first source that hands one over.

Only public promo listings are read. Nothing here touches an account, and the
app still stores no card of anyone's - a promo names which card *qualifies*,
which is a fact about the offer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import httpx

from apps.core.categories import SpendCategory

REQUEST_TIMEOUT = 90

# The page is ~12MB because the whole promo catalogue ships inside it. Worth
# saying out loud so the timeout above does not look arbitrary.
NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)

# "50% OFF at Domino's Pizza", "Up to 65% OFF at …", "3+1 Lunch Promo at …"
DISCOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:OFF|off|discount)")
FIXED_PRICE = re.compile(r"(?:for|at|only)\s*(?:P|₱|PHP)\s*([\d,]+(?:\.\d{2})?)", re.I)
# The merchant is almost always what follows " at ".
MERCHANT = re.compile(r"\bat\s+(.+?)\s*$", re.I)

# Metrobank's own categories, mapped onto the app's spend categories. Shopping
# covers everything from clothes to supplements, so it lands on apparel only as
# a default - a brand match against the places table overrides it below.
CATEGORY_MAP = {
    "Dining": SpendCategory.DINING,
    "Shopping": SpendCategory.APPAREL,
    "Travel": SpendCategory.TRANSPORT,
    "Services": SpendCategory.OTHER,
    "Online": SpendCategory.OTHER,
    "Installment": SpendCategory.OTHER,
    "Loans": SpendCategory.OTHER,
    "Special Offers": SpendCategory.OTHER,
    "Groceries": SpendCategory.GROCERY,
    "Health and Wellness": SpendCategory.HEALTH,
}

# When every card in the issuer's range qualifies, naming them all is noise.
GENERIC_CARD_COUNT = 4


@dataclass
class BankPromo:
    """One promo as published, before it becomes a Promo row."""

    reference: str
    title: str
    detail: str
    brand: str
    category: str
    card_name: str
    discount_pct: Decimal | None
    price: Decimal | None
    starts_on: date | None
    ends_on: date | None
    source_url: str


def _parse_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return None


# Invisible characters the export carries through: zero-width space, zero-width
# non-joiner/joiner, BOM, and the replacement character left where a non-ASCII
# glyph was mangled. They survive a strip() and re-appear as encoding errors or
# as titles that will not match on re-import.
INVISIBLE = re.compile(r"[​-‍﻿�]")


def _clean(text: str) -> str:
    """Tidy what the export emits, including what it emits invisibly."""
    if not text:
        return ""
    text = INVISIBLE.sub("", text).replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


# Phrases that follow "at" but name a situation rather than a merchant:
# "at select restaurants in Hong Kong", "at participating stores". Treating
# these as brands puts nonsense in the brand column and, worse, makes them
# unmatchable against the places table.
NOT_A_BRAND = re.compile(
    r"^(select|participating|any|all|various|over \d+|more than|its|their)\b",
    re.IGNORECASE,
)

# A brand is a name, not a sentence. Four words is generous for "New World
# Makati Hotel" while rejecting a clause.
MAX_BRAND_WORDS = 5


def _merchant_from(title: str) -> str:
    """The merchant a promo names, or blank when it names a situation.

    Blank is a perfectly good answer: the promo still lists under its category,
    it simply will not claim to be tied to a chain it is not tied to.
    """
    match = MERCHANT.search(title)
    if not match:
        return ""

    candidate = _clean(match.group(1))
    # Leading articles are noise: "the SM Shoes and Bags Sale" is the SM sale.
    candidate = re.sub(r"^(the|a|an)\s+", "", candidate, flags=re.IGNORECASE)

    if not candidate or NOT_A_BRAND.match(candidate):
        return ""
    if len(candidate.split()) > MAX_BRAND_WORDS:
        return ""
    return candidate


def _category_for(raw_categories, brand: str) -> str:
    """Map to a spend category, preferring what the brand actually is.

    A brand match against the places table beats the bank's own label: "50% OFF
    at Domino's Pizza" is filed by Metrobank under Shopping, but the app has
    Domino's mapped as fast food, and dining is the more useful answer.
    """
    from apps.core.categories import category_for_place
    from apps.places.models import Place

    if brand:
        place = (
            Place.objects.filter(brand__iexact=brand)
            .values_list("kind", flat=True)
            .first()
        )
        if place:
            return category_for_place(place)

    for entry in raw_categories or []:
        if isinstance(entry, dict):
            mapped = CATEGORY_MAP.get(entry.get("category", ""))
            if mapped:
                return mapped
    return SpendCategory.OTHER


def _cards_from(raw_cards) -> str:
    names = [
        _clean(c.get("title") if isinstance(c, dict) else str(c))
        for c in (raw_cards or [])
    ]
    names = [n for n in names if n]
    if not names or len(names) >= GENERIC_CARD_COUNT:
        # Blank means "any card from this issuer", which is what a full list
        # amounts to and is far easier to read.
        return ""
    return ", ".join(names[:3])


def parse_metrobank(html: str, *, base_url: str) -> list[BankPromo]:
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
        title = _clean(record.get("title", ""))
        if not title:
            continue

        brand = _merchant_from(title)
        discount = DISCOUNT.search(title)
        price = FIXED_PRICE.search(title)
        slug = record.get("slug") or ""

        promos.append(BankPromo(
            reference=slug or title[:120],
            title=title[:160],
            detail=_clean(record.get("description", ""))[:250],
            brand=brand[:60],
            category=_category_for(record.get("categories"), brand),
            card_name=_cards_from(record.get("cards"))[:120],
            discount_pct=Decimal(discount.group(1)) if discount else None,
            price=(
                Decimal(price.group(1).replace(",", "")) if price and not discount
                else None
            ),
            starts_on=_parse_date(record.get("startDate")),
            ends_on=_parse_date(record.get("expirationDate")),
            source_url=f"{base_url.rstrip('/')}{slug}" if slug else base_url,
        ))
    return promos


SOURCES = {
    "metrobank": {
        "issuer": "Metrobank",
        "url": "https://www.metrobank.com.ph/promos",
        "base": "https://www.metrobank.com.ph",
        "parser": parse_metrobank,
    },
}


def fetch(bank: str) -> list[BankPromo]:
    source = SOURCES[bank]
    response = httpx.get(
        source["url"],
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "OneApp/1.0 (personal budgeting app; promo reader)"},
    )
    response.raise_for_status()
    return source["parser"](response.text, base_url=source["base"])
