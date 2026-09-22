"""
Shared machinery for reading a bank's published promo listing.

Each bank gets its own parser because no two publish the same shape, but they
all face the same three problems, which is what lives here:

  * pulling a usable end date out of prose - the field that decides whether a
    promo is worth anything, and the one every bank formats differently;
  * working out which merchant a promo names, without inventing one;
  * mapping the bank's own labels onto the app's spend categories.

Only public promo listings are read. Nothing here touches an account, and the
app stores no card of anyone's - a promo names which card *qualifies*, which is
a fact about the offer, not about the reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from apps.core.categories import SpendCategory

REQUEST_TIMEOUT = 90
USER_AGENT = "OneApp/1.0 (personal budgeting app; promo reader)"


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


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

# Invisible characters these exports carry through: zero-width space, zero-width
# non-joiner/joiner, BOM, and the replacement character left where a non-ASCII
# glyph was mangled. They survive a strip() and re-appear as encoding errors or
# as titles that will not match on re-import.
INVISIBLE = re.compile("[​‌‍﻿�]")


def clean(text: str) -> str:
    """Tidy what a page emits, including what it emits invisibly."""
    if not text:
        return ""
    text = INVISIBLE.sub("", text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DATE_TOKEN = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:,\s*)?(20\d\d)?",
    re.IGNORECASE,
)


def _tokens(text: str) -> list[tuple[int, int, int | None]]:
    found = []
    for match in DATE_TOKEN.finditer(text):
        month = MONTHS[match.group(1)[:3].lower()]
        day = int(match.group(2))
        if not 1 <= day <= 31:
            continue
        year = int(match.group(3)) if match.group(3) else None
        found.append((month, day, year))
    return found


def _build(month: int, day: int, year: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        # 31 February and friends. A bad date is a parse failure, not a promo.
        return None


def promo_dates(*texts: str, today: date | None = None
                ) -> tuple[date | None, date | None]:
    """The start and end a promo advertises, from the first text that has any.

    Callers pass their candidates most-specific first - a dedicated date field,
    then the "promo period is from ..." sentence, then the whole page - so a
    precise answer always beats a lucky one found in the small print.

    A lone date is treated as the end date. That is the useful reading: banks
    write "Valid until 31 December" far more often than they write a start, and
    guessing the wrong end is the one mistake that matters.
    """
    today = today or date.today()

    for text in texts:
        if not text:
            continue
        found = _tokens(text)
        if not found:
            continue

        # "August 15 - December 31, 2026" states the year once, at the end.
        years = [t[2] for t in found if t[2]]
        filled = []
        for index, (month, day, year) in enumerate(found):
            if year is None:
                later = next((t[2] for t in found[index + 1:] if t[2]), None)
                year = later or (years[0] if years else today.year)
            filled.append((month, day, year))

        if len(filled) == 1:
            return None, _build(*filled[0])

        start = _build(*filled[0])
        end = _build(*filled[1])
        if start and end and start > end and not found[0][2]:
            # "December 15 - January 31, 2027" runs across new year, and only
            # the end date said so.
            start = _build(filled[0][0], filled[0][1], filled[0][2] - 1)
        return start, end

    return None, None


# --------------------------------------------------------------------------
# Amounts
# --------------------------------------------------------------------------

DISCOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:OFF|off|discount|savings)", re.I)
FIXED_PRICE = re.compile(
    r"(?:for|at|only)\s*(?:P|₱|PHP|Php)\s*([\d,]+(?:\.\d{2})?)", re.I
)


def offer_from(text: str) -> tuple[Decimal | None, Decimal | None]:
    """The discount percentage or the fixed price a promo advertises."""
    discount = DISCOUNT.search(text or "")
    if discount:
        try:
            return Decimal(discount.group(1)), None
        except InvalidOperation:
            return None, None

    price = FIXED_PRICE.search(text or "")
    if price:
        try:
            return None, Decimal(price.group(1).replace(",", ""))
        except InvalidOperation:
            return None, None
    return None, None


# --------------------------------------------------------------------------
# Merchants
# --------------------------------------------------------------------------

# The merchant follows "at", and on some sites "with" or "by": "15% savings at
# Serenitea", "VYBE into Bold Flavors with Propaganda Bistro", "La Terraza by
# Alta D' Tagaytay Hotel".
MERCHANT = re.compile(r"\b(?:at|with|by)\s+(.+?)\s*$", re.I)

# Phrases that follow the preposition but name a situation rather than a
# merchant: "at select restaurants in Hong Kong", "at participating stores",
# "with your BPI credit card". Treating these as brands puts nonsense in the
# brand column and, worse, makes them unmatchable against the places table.
NOT_A_BRAND = re.compile(
    r"^(select|participating|any|all|various|over \d+|more than|its|their"
    r"|your|our|my|every|up to)\b",
    re.IGNORECASE,
)

# A payment instrument is never the merchant, however the sentence reads.
NOT_A_MERCHANT = re.compile(
    r"\b(?:credit|debit|prepaid)\s+card|\bcard(?:holder)?s?\b|\bapp\b|\baccount\b"
    # Nor is the offer itself: "No Annual Fee For Life" is a waiver, not a shop.
    r"|\b(?:fee|cashback|rebate|installment|discount|voucher)s?\b",
    re.IGNORECASE,
)

# A brand is a name, not a sentence. Five words is generous for "New World
# Makati Hotel" while rejecting a clause.
MAX_BRAND_WORDS = 5


def merchant_from(title: str) -> str:
    """The merchant a promo names, or blank when it names a situation.

    Blank is a perfectly good answer: the promo still lists under its category,
    it simply will not claim to be tied to a chain it is not tied to.
    """
    match = MERCHANT.search(title or "")
    if not match:
        return ""

    candidate = clean(match.group(1))
    # Leading articles are noise: "the SM Shoes and Bags Sale" is the SM sale.
    # Lowercase only - "A Lounge" is a bar, not an article and a noun.
    candidate = re.sub(r"^(?:the|a|an)\s+", "", candidate)
    # Trailing qualifiers: "Serenitea when you pay with your EastWest card",
    # "Shakey's, exclusively with your BankCom card".
    candidate = re.split(
        r"\s*[,;:]\s*"
        r"|\s+(?:when|with|using|for|via|and get|plus|exclusively|every|exclusive)\b",
        candidate, flags=re.I,
    )[0].strip(" .,!-–—")
    # "Alta D' Tagaytay Hotel Promo" is a hotel with a promo, not a shop called
    # Promo.
    candidate = re.sub(r"\s+(?:promo|offer|deal)s?$", "", candidate, flags=re.I)

    if not candidate or NOT_A_BRAND.match(candidate):
        return ""
    # "0% installment", "5,000 CASH REBATE" - an offer, not a shop.
    if not candidate[0].isalpha():
        return ""
    if NOT_A_MERCHANT.search(candidate):
        return ""
    if len(candidate.split()) > MAX_BRAND_WORDS:
        return ""
    return candidate


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------

# Whatever a bank calls its own sections, mapped onto the app's categories.
# Shopping covers everything from clothes to supplements, so it lands on apparel
# only as a default - a brand match against the places table overrides it.
LABEL_MAP = {
    "dining": SpendCategory.DINING,
    "food": SpendCategory.DINING,
    "restaurants": SpendCategory.DINING,
    "shopping": SpendCategory.APPAREL,
    "retail": SpendCategory.APPAREL,
    "fashion": SpendCategory.APPAREL,
    "travel": SpendCategory.TRANSPORT,
    "hotels": SpendCategory.TRANSPORT,
    "transport": SpendCategory.TRANSPORT,
    "groceries": SpendCategory.GROCERY,
    "grocery": SpendCategory.GROCERY,
    "supermarket": SpendCategory.GROCERY,
    "health and wellness": SpendCategory.HEALTH,
    "health": SpendCategory.HEALTH,
    "wellness": SpendCategory.HEALTH,
    "beauty": SpendCategory.HEALTH,
    "services": SpendCategory.OTHER,
    "online": SpendCategory.OTHER,
    "installment": SpendCategory.OTHER,
    "loans": SpendCategory.OTHER,
    "lifestyle": SpendCategory.OTHER,
}


def category_for(labels, brand: str) -> str:
    """Map to a spend category, preferring what the brand actually is.

    A brand match against the places table beats the bank's own label: "50% OFF
    at Domino's Pizza" is filed by Metrobank under Shopping, but the app has
    Domino's mapped as fast food, and dining is the more useful answer.
    """
    from apps.core.categories import category_for_place
    from apps.places.models import Place

    if brand:
        kind = (
            Place.objects.filter(brand__iexact=brand)
            .values_list("kind", flat=True)
            .first()
        )
        if kind:
            return category_for_place(kind)

    for label in labels or []:
        mapped = LABEL_MAP.get(clean(str(label)).lower())
        if mapped:
            return mapped
    return SpendCategory.OTHER


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

def soup(html: str):
    """Parse with the stdlib backend - no lxml, so no compiler on Windows."""
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def text_of(node, default: str = "") -> str:
    return clean(node.get_text(" ", strip=True)) if node else default


def get(url: str) -> str:
    """Fetch a public promo page.

    Identifies itself honestly. Every site read here either allows this path in
    robots.txt or publishes no robots.txt at all; the two that answer 403 to a
    non-browser client are left alone rather than worked around.
    """
    import httpx

    response = httpx.get(
        url,
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.text
