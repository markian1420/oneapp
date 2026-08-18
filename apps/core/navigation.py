"""
The sidebar, as data.

One list drives the navigation so a new module is a single entry rather than an
edit in a template. Modules are grouped by the question they answer, not by the
Django app they live in - fuel, groceries and utilities are all "where is the
money going", and the user does not care how the code is packaged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    code: str
    name: str
    icon: str
    url_name: str
    group: str


MODULES: tuple[Module, ...] = (
    Module("home", "Overview", "layout-dashboard", "core:home", ""),
    Module("insights", "Today", "sparkles", "insights:briefing", ""),
    Module("places_map", "Map", "map", "places:map", ""),
    Module("fuel_map", "Fuel map", "fuel", "fuel:map", "Fuel"),
    Module("fuel_stations", "Stations", "map-pin", "fuel:stations", "Fuel"),
    Module("fuel_fillups", "Fill-ups", "receipt", "fuel:fillups", "Fuel"),
    Module("fuel_advisory", "DOE advisory", "scroll-text", "fuel:advisory", "Fuel"),
    Module("fuel_vehicles", "Vehicles", "car", "fuel:vehicles", "Fuel"),
    Module("grocery_prices", "Commodity prices", "basket", "grocery:commodities", "Grocery"),
    Module("spend_where", "Where to buy", "basket", "spend:where", "Spend"),
    Module("spend_purchases", "Spending", "receipt", "spend:purchases", "Spend"),
    Module("spend_promos", "Merchant promos", "tag", "spend:promos", "Spend"),
    Module("spend_card_promos", "Card promos", "credit-card", "spend:card_promos", "Spend"),
    Module("spend_products", "Product watch", "tag", "spend:products", "Spend"),
    Module("spend_wardrobe", "Wardrobe", "shirt", "spend:wardrobe", "Spend"),
)


def grouped_modules() -> list[dict]:
    """Consecutive runs of the same group, in declaration order.

    Grouping by consecutive run rather than by collecting everything with a
    matching name keeps the tuple above the single source of truth for order:
    a module sits where it is written, and its heading follows it.
    """
    groups: list[dict] = []
    for module in MODULES:
        if groups and groups[-1]["name"] == module.group:
            groups[-1]["modules"].append(module)
        else:
            groups.append({"name": module.group, "modules": [module]})
    return groups
