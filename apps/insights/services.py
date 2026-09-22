"""
What the app has worked out from the remaining tracked data.

Deliberately explainable signals rather than a model. At the volumes this app
produces, anything opaque would be fitting noise and presenting it with a
straight face.

Every insight carries how confident it is and what it was computed from, so a
thin one reads as thin instead of arriving with the same authority as a solid
one. It also means the app can say "not yet" - the honest answer for most of
these on day one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.grocery.services import biggest_movers
from apps.spend.models import Purchase
from apps.spend.services import card_promos_at

# Below this many observations an insight is offered as a hint rather than a
# finding. Five is not statistically meaningful; it is simply the point where a
# pattern stops being a single coincidence.
THIN_EVIDENCE = 5
SOLID_EVIDENCE = 12


@dataclass
class Insight:
    """One thing the app noticed, with its evidence attached."""

    key: str
    headline: str
    detail: str = ""
    tone: str = "info"          # info | good | warn | bad
    observations: int = 0
    action_url: str = ""
    action_label: str = ""

    @property
    def confidence(self) -> str:
        if self.observations >= SOLID_EVIDENCE:
            return "solid"
        if self.observations >= THIN_EVIDENCE:
            return "fair"
        return "thin"

    @property
    def confidence_note(self) -> str:
        return {
            "solid": f"from {self.observations} observations",
            "fair": f"from only {self.observations} observations",
            "thin": f"from just {self.observations} - treat as a hint",
        }[self.confidence]

    @property
    def badge_class(self) -> str:
        return {
            "good": "badge-success", "warn": "badge-warning",
            "bad": "badge-danger", "info": "badge-info",
        }[self.tone]


@dataclass
class Briefing:
    """Everything worth saying today, in one object."""

    insights: list[Insight] = field(default_factory=list)
    learning: list[str] = field(default_factory=list)

    @property
    def has_anything(self) -> bool:
        return bool(self.insights)


# ------------------------------------------------------------- grocery ----

def grocery_swing() -> Insight | None:
    movers = biggest_movers(limit=6)
    if not movers:
        return None

    risers = [m for m in movers if m["pct"] > 10]
    fallers = [m for m in movers if m["pct"] < -5]

    if not risers and not fallers:
        return None

    if fallers:
        best = fallers[0]
        return Insight(
            key="grocery_swing",
            headline=(
                f"{best['commodity'].name} is down {abs(best['pct'])}% this week"
            ),
            detail=(
                f"Now {best['movement'].latest:.2f} per {best['commodity'].unit}."
                + (
                    f" Meanwhile {risers[-1]['commodity'].name} is up "
                    f"{risers[-1]['pct']}% - worth skipping."
                    if risers else ""
                )
            ),
            tone="good",
            observations=best["movement"].points,
            action_url="/grocery/",
            action_label="See all prices",
        )

    worst = risers[-1]
    return Insight(
        key="grocery_swing",
        headline=f"{worst['commodity'].name} is up {worst['pct']}% this week",
        detail=f"Now {worst['movement'].latest:.2f} per {worst['commodity'].unit}.",
        tone="warn",
        observations=worst["movement"].points,
        action_url="/grocery/",
        action_label="See all prices",
    )


def card_promo_coverage() -> Insight | None:
    """Card promos running where you actually shop.

    Built from the places in your own purchase history rather than the whole
    promo list, so it surfaces the ones you could plausibly use. The app holds
    no card of yours - this only says an offer exists, never that you have the
    card for it.
    """
    places = [
        p.place for p in
        Purchase.objects.exclude(place__isnull=True).select_related("place")
        .order_by("-occurred_at")[:40]
    ]
    if not places:
        return None

    seen, matches = set(), []
    for place in places:
        if place.pk in seen:
            continue
        seen.add(place.pk)
        for promo in card_promos_at(place):
            matches.append((place, promo))

    if not matches:
        return None

    place, promo = matches[0]
    return Insight(
        key="card_promo_coverage",
        headline=(
            f"{len(matches)} card promo{'s' if len(matches) > 1 else ''} running "
            "where you shop"
        ),
        detail=f"{promo.qualifies} - {promo.title} at {place.display_name}.",
        tone="info",
        observations=len(seen),
        action_url="/spend/card-promos/",
        action_label="See card promos",
    )


# ------------------------------------------------------------ assembly ----

BUILDERS = (
    grocery_swing,
    card_promo_coverage,
)

# What each insight needs before it can say anything, so the app can explain
# its own silence instead of just looking empty.
REQUIREMENTS = {
    "grocery_swing": "a few days of DA prices imported",
    "card_promo_coverage": "a card promo entered for somewhere you shop",
}


def build_briefing() -> Briefing:
    """Everything the data currently supports saying."""
    insights = []
    produced = set()

    for builder in BUILDERS:
        insight = builder()
        if insight:
            insights.append(insight)
            produced.add(insight.key)

    # Sort by how much attention it deserves, then by evidence.
    order = {"bad": 0, "warn": 1, "good": 2, "info": 3}
    insights.sort(key=lambda i: (order[i.tone], -i.observations))

    learning = [
        need for key, need in REQUIREMENTS.items() if key not in produced
    ]
    return Briefing(insights=insights, learning=learning)
