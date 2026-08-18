"""
Tracking a specific product across sellers, and how far each can be trusted.

There is no price feed for this. No Philippine retailer publishes product-level
prices in any machine-readable form: Shopee and Lazada expose seller-side APIs
only, and the brands' own stores are client-rendered with no product schema -
Salomon PH's page carries a single Organization block and prices as loose text
not bound to any SKU. Scraping it would break on the next redesign and, worse,
would sometimes report the wrong shoe's price with total confidence.

So the app does not pretend to search. It tracks what *you* find, and it does
the part software is actually good at: remembering which sellers are which, and
noticing when a domain is wearing a brand's name without permission.

That last part is the real risk here. A search for "Salomon XT-6" returns
ph.salomon.com and salomophilippines.com side by side - one is the brand, the
other is a misspelling. Sorting by price alone would happily recommend the fake.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

# How much a seller can be trusted, and why. Ordered: lower is safer.
TRUST_ORDER = {
    "official": 0,
    "authorised": 1,
    "marketplace": 2,
    "unverified": 3,
}

TRUST_LABEL = {
    "official": "Brand's own store",
    "authorised": "Authorised stockist",
    "marketplace": "Marketplace seller",
    "unverified": "Unverified",
}

TRUST_NOTE = {
    "official": "Sold by the brand itself. As safe as it gets.",
    "authorised": "A stockist you have confirmed the brand recognises.",
    "marketplace": (
        "A Shopee or Lazada listing. Genuine and counterfeit sit side by side "
        "there, so the price alone says nothing about the shoe."
    ),
    "unverified": (
        "Nobody has checked this seller. Treat the price as unconfirmed."
    ),
}

# Domains that are the brand itself. Only the ones actually verified go here -
# an unverified guess in this table is worse than no table at all.
KNOWN_OFFICIAL_DOMAINS = {
    "salomon": {"salomon.com", "ph.salomon.com", "phl.salomon.com"},
    "nike": {"nike.com", "nike.com.ph"},
    "adidas": {"adidas.com", "adidas.com.ph"},
    "uniqlo": {"uniqlo.com"},
    "new balance": {"newbalance.com", "newbalance.com.ph"},
    "asics": {"asics.com"},
    "hoka": {"hoka.com"},
    "on": {"on.com", "on-running.com"},
}

MARKETPLACE_DOMAINS = {
    "shopee.ph", "lazada.com.ph", "carousell.ph", "tiktok.com",
    "facebook.com", "instagram.com",
}

# Close enough to a brand name to be mistaken for it at a glance.
LOOKALIKE_RATIO = 0.82


def domain_of(url: str) -> str:
    """The registrable-ish host, lowercased and without www."""
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _brand_tokens(domain: str) -> list[str]:
    """The word-ish parts of a hostname, minus the boring bits."""
    stem = domain.rsplit(".", 2)[0] if domain.count(".") >= 2 else domain.split(".")[0]
    parts = re.split(r"[^a-z0-9]+", stem)
    noise = {"shop", "store", "official", "ph", "philippines", "online", "www"}
    return [p for p in parts if p and p not in noise] or [stem]


def classify(url: str, brand: str) -> str:
    """Best guess at what kind of seller a URL is.

    A guess, and labelled as one everywhere it surfaces: the only tiers the app
    asserts on its own are the brand's verified domains and the known
    marketplaces. Everything else starts unverified until you say otherwise.
    """
    domain = domain_of(url)
    if not domain:
        return "unverified"

    official = KNOWN_OFFICIAL_DOMAINS.get((brand or "").strip().lower(), set())
    if domain in official or any(domain.endswith("." + d) for d in official):
        return "official"

    if domain in MARKETPLACE_DOMAINS or any(
        domain.endswith("." + d) for d in MARKETPLACE_DOMAINS
    ):
        return "marketplace"

    return "unverified"


@dataclass
class DomainWarning:
    """A seller whose domain is wearing a brand's name."""

    domain: str
    resembles: str
    detail: str


def lookalike_warning(url: str, brand: str) -> DomainWarning | None:
    """Flag a domain that imitates the brand without being it.

    The case this exists for is real and was found while building it: searching
    "Salomon XT-6 Philippines" returns ph.salomon.com and salomophilippines.com
    together. The second is one letter short of the brand and is not the brand.

    Deliberately a warning, not a block. The app cannot know that a lookalike is
    a scam, only that it looks like one - and saying so is enough for a person
    to check before parting with 12,000 pesos.
    """
    domain = domain_of(url)
    brand = (brand or "").strip().lower()
    if not domain or not brand:
        return None

    official = KNOWN_OFFICIAL_DOMAINS.get(brand, set())
    if domain in official or any(domain.endswith("." + d) for d in official):
        return None
    if domain in MARKETPLACE_DOMAINS or any(
        domain.endswith("." + d) for d in MARKETPLACE_DOMAINS
    ):
        return None

    for token in _brand_tokens(domain):
        if token == brand:
            # Contains the brand exactly but is not an official domain. Could
            # be a legitimate multi-brand retailer, so this is the softer note.
            return DomainWarning(
                domain=domain,
                resembles=brand,
                detail=(
                    f"Uses the {brand.title()} name but is not one of the "
                    "brand's own domains. That is normal for a stockist and "
                    "normal for a fake - worth confirming which."
                ),
            )

        # Compare against the start of the token as well as the whole of it.
        # The case this was built for, "salomophilippines", is one unbroken
        # word: whole-token similarity to "salomon" is only 0.58, while its
        # first six characters ("salomo") score 0.92. Without the prefix pass
        # the exact domain that prompted this check sails straight through.
        candidates = {token}
        for width in (len(brand) - 1, len(brand), len(brand) + 1):
            if 0 < width <= len(token):
                candidates.add(token[:width])

        for candidate in candidates:
            if candidate == brand:
                continue
            ratio = difflib.SequenceMatcher(None, candidate, brand).ratio()
            if ratio >= LOOKALIKE_RATIO:
                return DomainWarning(
                    domain=domain,
                    resembles=brand,
                    detail=(
                        f'"{candidate}" is a near-miss of "{brand}" rather than '
                        "the name itself. Misspelled domains are the standard "
                        "counterfeit pattern - check this one carefully."
                    ),
                )
    return None


@dataclass
class Quote:
    """One recorded price for a product at one seller."""

    seller: str
    url: str
    price: Decimal
    trust: str
    seen_on: object
    in_stock: bool
    size: str = ""
    warning: DomainWarning | None = None

    @property
    def trust_label(self) -> str:
        return TRUST_LABEL[self.trust]

    @property
    def trust_note(self) -> str:
        return TRUST_NOTE[self.trust]

    @property
    def badge_class(self) -> str:
        return {
            "official": "badge-success",
            "authorised": "badge-info",
            "marketplace": "badge-warning",
            "unverified": "badge-muted",
        }[self.trust]

    @property
    def is_trusted(self) -> bool:
        return self.trust in {"official", "authorised"}


def rank_quotes(quotes: list[Quote]) -> list[Quote]:
    """Cheapest first, but never mixing trust tiers together.

    Sorting purely on price puts an unverified 6,000-peso listing above the
    brand's own 12,990, which reads as a recommendation to buy the suspicious
    one. Trust decides the grouping; price decides the order inside it.
    """
    return sorted(quotes, key=lambda q: (TRUST_ORDER[q.trust], q.price))


def best_trusted(quotes: list[Quote]) -> Quote | None:
    trusted = [q for q in quotes if q.is_trusted and q.in_stock]
    return min(trusted, key=lambda q: q.price) if trusted else None


def cheapest_overall(quotes: list[Quote]) -> Quote | None:
    available = [q for q in quotes if q.in_stock]
    return min(available, key=lambda q: q.price) if available else None
