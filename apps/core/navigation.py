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
    Module("places_calibration", "Where am I", "locate", "places:calibration", ""),
    Module("fuel_map", "Fuel map", "fuel", "fuel:map", "Fuel"),
    Module("fuel_advisory", "DOE advisory", "scroll-text", "fuel:advisory", "Fuel"),
    Module("grocery_map", "Grocery map", "map", "grocery:map", "Grocery"),
    Module("grocery_prices", "Commodity prices", "basket", "grocery:commodities", "Grocery"),
    Module("spend_where", "Where to buy", "basket", "spend:where", "Shopping"),
    Module("spend_card_promos", "Card promos", "credit-card", "spend:card_promos", "Shopping"),
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
