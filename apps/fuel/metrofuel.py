"""
Parser for the MetroFuel Tracker public prices page.

Complements the GasWatch import rather than replacing it. GasWatch gives a
Metro Manila price band across five grades but no brand; this gives per-brand
averages, which is what the price resolver actually wants - a Shell station
getting Shell's number instead of the whole region's median.

Read from https://metrofueltracker.com/prices, which their robots.txt allows
(`Allow: /`). Their `/api/` is explicitly disallowed and is not touched: the
page renders everything server-side, so there is no need to go near it.

Two honest limits, both carried through to the UI:

  * the brand averages are national, computed across 153 cities, so they are a
    brand signal rather than a local one;
  * only diesel and unleaded 91 are published per brand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .models import FuelType

PRICES_URL = "https://metrofueltracker.com/prices"

# Brands the page lists. Anchoring on a known list rather than "any capitalised
# word" is what stops a section heading being read as a brand.
KNOWN_BRANDS = [
    "Petron", "Shell", "Caltex", "Seaoil", "Unioil", "Phoenix", "Cleanfuel",
    "PTT", "Total", "Flying V", "Jetti", "Nitrofuel", "Insular", "Filoil",
    "Eastern Petroleum", "Uno Fuel", "Clean Fuel", "Petro Gazz", "Jetty",
]

# How the page labels each grade, mapped to ours.
GRADE_LABELS = {
    "Avg Diesel": FuelType.DIESEL,
    "Avg Unleaded 91": FuelType.GAS_91,
}

NATIONAL_LABELS = {
    "Diesel": FuelType.DIESEL,
    "Premium Diesel": FuelType.DIESEL_PREMIUM,
    "Unleaded 91": FuelType.GAS_91,
    "Premium 95": FuelType.GAS_95,
    "Premium 97/98/100": FuelType.GAS_97,
}

UPDATED = re.compile(r"Updated\s*\|?\s*([A-Z][a-z]+ \d{1,2}, \d{4})")


@dataclass
class BrandPrices:
    brand: str
    stations: int
    prices: dict[str, Decimal]


@dataclass
class Parsed:
    updated_on: date | None = None
    stations: int = 0
    cities: int = 0
    national: dict[str, Decimal] = None
    brands: list[BrandPrices] = None

    def __post_init__(self):
        self.national = self.national or {}
        self.brands = self.brands or []


def to_text(html: str) -> str:
    """Flatten the markup to pipe-separated text, as the page reads."""
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
    body = re.sub(r"<[^>]+>", "|", body)
    body = re.sub(r"\|+", "|", body)
    return re.sub(r"\s+", " ", body)


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except Exception:
        return None


def parse(html: str) -> Parsed:
    """Pull the national averages and the per-brand averages off the page."""
    text = to_text(html)
    result = Parsed()

    stamp = UPDATED.search(text)
    if stamp:
        try:
            result.updated_on = datetime.strptime(
                stamp.group(1), "%B %d, %Y"
            ).date()
        except ValueError:
            pass

    counts = re.search(r"(\d[\d,]*)\|?\s*stations?\s*·\s*\|?(\d[\d,]*)\|?\s*cities", text)
    if counts:
        result.stations = int(counts.group(1).replace(",", ""))
        result.cities = int(counts.group(2).replace(",", ""))

    # ---- national averages ---------------------------------------------
    national_block = text.find("National Average Prices")
    if national_block != -1:
        chunk = text[national_block:national_block + 400]
        for label, grade in NATIONAL_LABELS.items():
            match = re.search(
                re.escape(label) + r"\s*\|?\s*₱\s*\|?\s*([\d,]+\.\d{2})", chunk
            )
            if match:
                value = _decimal(match.group(1))
                if value:
                    result.national[grade] = value

    # ---- per-brand averages --------------------------------------------
    brand_block = text.find("Prices by Brand")
    if brand_block == -1:
        return result

    tail = text[brand_block:]
    # Where each brand's row starts, in the order the page lists them.
    positions = []
    for brand in KNOWN_BRANDS:
        for match in re.finditer(
            r"\|" + re.escape(brand) + r"\|(\d[\d,]*)\|?\s*stations?", tail
        ):
            positions.append((match.start(), brand, int(match.group(1).replace(",", ""))))
    positions.sort()

    for index, (start, brand, stations) in enumerate(positions):
        # Stop at the next brand. This is the whole trick: Cleanfuel is listed
        # with 83 stations and no prices at all, so a parser that simply took
        # the next two numbers would hand it PTT's figures and never say so.
        end = positions[index + 1][0] if index + 1 < len(positions) else start + 400
        segment = tail[start:end]

        prices: dict[str, Decimal] = {}
        for label, grade in GRADE_LABELS.items():
            match = re.search(
                re.escape(label) + r"\s*\|?\s*₱\s*\|?\s*([\d,]+\.\d{2})", segment
            )
            if match:
                value = _decimal(match.group(1))
                if value:
                    prices[grade] = value

        result.brands.append(
            BrandPrices(brand=brand, stations=stations, prices=prices)
        )

    return result
