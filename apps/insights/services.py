"""
What the app has worked out from your own data.

Deliberately explainable statistics rather than a model. At the volumes a
personal budget produces - tens of fill-ups, hundreds of purchases - anything
opaque would be fitting noise and presenting it with a straight face. Rolling
medians, day-of-week effects and simple deltas are things you can check against
your own memory, which is what makes advice worth following.

Every insight carries how confident it is and what it was computed from, so a
thin one reads as thin instead of arriving with the same authority as a solid
one. It also means the app can say "not yet" - the honest answer for most of
these on day one.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Sum
from django.utils import timezone

from apps.cards.services import rank_cards
from apps.core.categories import SpendCategory, category_for_place
from apps.fuel.models import FillUp
from apps.fuel.services import fuel_economy
from apps.grocery.services import biggest_movers
from apps.spend.models import Purchase, PurchaseItem
from apps.spend.services import expiring_promos, live_promos, spend_by_category

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


# ---------------------------------------------------------------- fuel ----

def fuel_rhythm() -> Insight | None:
    """How often you refuel, and therefore when you are next due.

    Uses the median gap rather than the mean: one holiday road trip would drag
    an average badly, and the question being answered is "what is normal".
    """
    fills = list(FillUp.objects.order_by("filled_at").only("filled_at"))
    if len(fills) < 3:
        return None

    gaps = [
        (b.filled_at - a.filled_at).days
        for a, b in zip(fills, fills[1:])
        if (b.filled_at - a.filled_at).days > 0
    ]
    if not gaps:
        return None

    typical = int(statistics.median(gaps))
    since = (timezone.now() - fills[-1].filled_at).days
    due_in = typical - since

    if due_in <= 0:
        headline = f"You are due to refuel - {since} days since the last one"
        tone = "warn"
    else:
        headline = f"Next fill-up due in about {due_in} days"
        tone = "info"

    return Insight(
        key="fuel_rhythm",
        headline=headline,
        detail=f"You normally refuel every {typical} days.",
        tone=tone,
        observations=len(gaps),
        action_url="/fuel/",
        action_label="Find a station",
    )


def fuel_economy_insight() -> Insight | None:
    fills = list(FillUp.objects.order_by("-filled_at")[:20])
    economy = fuel_economy(fills)
    if economy is None:
        return None

    vehicle = fills[0].vehicle
    claimed = vehicle.km_per_liter
    drift = economy - claimed

    if abs(drift) < Decimal("0.5"):
        return Insight(
            key="economy",
            headline=f"Real economy is {economy} km/L, close to what you set",
            observations=len([f for f in fills if f.odometer_km]),
            tone="good",
        )

    direction = "better" if drift > 0 else "worse"
    return Insight(
        key="economy",
        headline=f"Real economy is {economy} km/L, {direction} than the {claimed} set",
        detail=(
            "Comparisons price the detour using this figure, so correcting it on "
            "the vehicle makes every station ranking more accurate."
        ),
        tone="good" if drift > 0 else "warn",
        observations=len([f for f in fills if f.odometer_km]),
        action_url=f"/fuel/vehicles/{vehicle.pk}/",
        action_label="Correct it",
    )


def cheapest_station_habit() -> Insight | None:
    """Whether you actually go where it is cheapest.

    Revealed preference against the receipts: the interesting case is a station
    you use often that is consistently dearer than another you also use.
    """
    rows = (
        FillUp.objects.values("place__id", "place__name", "place__brand")
        .annotate(visits=Count("id"), average=Avg("price_per_liter"))
        .filter(visits__gte=2)
        .order_by("average")
    )
    rows = list(rows)
    if len(rows) < 2:
        return None

    cheapest, dearest = rows[0], rows[-1]
    gap = dearest["average"] - cheapest["average"]
    if gap < Decimal("0.50"):
        return None

    name = lambda r: r["place__brand"] or r["place__name"] or "a station"  # noqa: E731
    return Insight(
        key="station_habit",
        headline=(
            f"{name(cheapest)} has averaged {gap:.2f}/L less than {name(dearest)}"
        ),
        detail=(
            f"Across {cheapest['visits']} and {dearest['visits']} visits. On a "
            f"40-litre tank that is about {gap * 40:.0f} pesos a fill."
        ),
        tone="info",
        observations=cheapest["visits"] + dearest["visits"],
        action_url="/fuel/",
        action_label="Compare on the map",
    )


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


# --------------------------------------------------------------- spend ----

def spend_drift() -> Insight | None:
    """The category moving most against last month."""
    rows = [r for r in spend_by_category() if r.previous or r.spent]
    if not rows:
        return None

    ranked = [r for r in rows if r.change_pct is not None]
    if not ranked:
        return None

    worst = max(ranked, key=lambda r: r.change_pct)
    if worst.change_pct < 15:
        return None

    return Insight(
        key="spend_drift",
        headline=f"{worst.label} is up {worst.change_pct}% on last month",
        detail=(
            f"{worst.spent:.0f} so far against {worst.previous:.0f} for the whole "
            "of last month."
        ),
        tone="warn" if worst.change_pct < 50 else "bad",
        observations=worst.count,
        action_url="/spend/",
        action_label="See the purchases",
    )


def card_leakage() -> Insight | None:
    """Money left on the table by tapping the wrong card.

    Only counts purchases where a card was recorded, and says how many, because
    the figure is only as complete as the logging behind it.
    """
    purchases = list(
        Purchase.objects.exclude(card__isnull=True)
        .select_related("card", "place")
        .order_by("-occurred_at")[:60]
    )
    if not purchases:
        return None

    lost = Decimal("0")
    missed = 0
    for purchase in purchases:
        picks = rank_cards(
            category=purchase.category,
            brand=purchase.place.brand if purchase.place else "",
            amount=purchase.total,
        )
        if not picks:
            continue
        best = picks[0]
        used = next((p for p in picks if p.card.pk == purchase.card_id), None)
        if used and best.value > used.value:
            lost += best.value - used.value
            missed += 1

    if not missed:
        return Insight(
            key="card_leakage",
            headline="You have been tapping the best card every time",
            detail=f"Checked across {len(purchases)} purchases with a card recorded.",
            tone="good",
            observations=len(purchases),
        )

    return Insight(
        key="card_leakage",
        headline=f"About {lost:.0f} pesos left behind on {missed} purchases",
        detail=(
            f"Out of {len(purchases)} where a card was recorded, a different card "
            "in your wallet would have earned more."
        ),
        tone="warn",
        observations=len(purchases),
        action_url="/cards/which/",
        action_label="Check before you tap",
    )


def promo_watch() -> Insight | None:
    expiring = expiring_promos()
    if not expiring:
        return None

    first = expiring[0]
    return Insight(
        key="promo_watch",
        headline=(
            f"{len(expiring)} promo{'s' if len(expiring) > 1 else ''} ending within 3 days"
        ),
        detail=f"Soonest: {first.title}"
               + (f" at {first.brand}" if first.brand else ""),
        tone="warn",
        observations=len(live_promos()),
        action_url="/spend/promos/",
        action_label="See promos",
    )


def idle_wardrobe() -> Insight | None:
    unworn = list(
        PurchaseItem.objects.filter(is_wearable=True, wears=0)
        .select_related("purchase")
    )
    if not unworn:
        return None

    spent = sum((i.amount for i in unworn), Decimal("0"))
    if spent < Decimal("500"):
        return None

    return Insight(
        key="idle_wardrobe",
        headline=f"{len(unworn)} tracked item{'s' if len(unworn) > 1 else ''} never worn",
        detail=f"About {spent:.0f} pesos sitting unused.",
        tone="warn",
        observations=len(unworn),
        action_url="/spend/wardrobe/",
        action_label="Open the wardrobe",
    )


# ------------------------------------------------------------ assembly ----

BUILDERS = (
    fuel_rhythm,
    fuel_economy_insight,
    cheapest_station_habit,
    grocery_swing,
    spend_drift,
    card_leakage,
    promo_watch,
    idle_wardrobe,
)

# What each insight needs before it can say anything, so the app can explain
# its own silence instead of just looking empty.
REQUIREMENTS = {
    "fuel_rhythm": "3 fill-ups, to know how often you refuel",
    "economy": "2 full tanks with odometer readings",
    "station_habit": "2 visits each to at least 2 stations",
    "grocery_swing": "a few days of DA prices imported",
    "spend_drift": "purchases logged in two different months",
    "card_leakage": "purchases logged with the card recorded",
    "promo_watch": "a promo with an end date",
    "idle_wardrobe": "clothing logged with wear tracking on",
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
