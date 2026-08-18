"""
Spend categories, shared by every module.

Cards earn on categories, budgets are set per category, and the insight layer
compares them. Defining this once means a card's "5% on dining" and a place
tagged as fast food are talking about the same thing without a translation
table in the middle.
"""

from __future__ import annotations

from django.db import models


class SpendCategory(models.TextChoices):
    FUEL = "fuel", "Fuel"
    GROCERY = "grocery", "Groceries"
    DINING = "dining", "Dining"
    APPAREL = "apparel", "Clothing"
    HEALTH = "health", "Health & pharmacy"
    UTILITIES = "utilities", "Utilities"
    TRANSPORT = "transport", "Transport"
    OTHER = "other", "Other"


# Where a kind of place puts your money. A pharmacy is health rather than
# groceries because that is how card issuers categorise it, and the whole point
# of this mapping is to answer "which card earns here".
PLACE_KIND_CATEGORY = {
    "fuel": SpendCategory.FUEL,
    "market": SpendCategory.GROCERY,
    "supermarket": SpendCategory.GROCERY,
    "convenience": SpendCategory.GROCERY,
    "fast_food": SpendCategory.DINING,
    "restaurant": SpendCategory.DINING,
    "mall": SpendCategory.APPAREL,
    "pharmacy": SpendCategory.HEALTH,
}


def category_for_place(kind: str) -> str:
    return PLACE_KIND_CATEGORY.get(kind, SpendCategory.OTHER)
