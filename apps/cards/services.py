"""
Which card to tap.

The whole module exists for one question, asked at a counter with a queue
behind you: of the cards in my wallet, which one earns most on this purchase?
It has to be answered in one glance, and it has to show its working, because a
recommendation you cannot check is one you will stop trusting the first time
it looks wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q

from apps.core.categories import SpendCategory, category_for_place

from .models import Card, Reward

# What a comparison assumes you are spending when you have not said. Enough to
# separate the cards without implying precision.
DEFAULT_BASKET = Decimal("1000")


@dataclass
class Pick:
    """One card, scored for a particular purchase."""

    card: Card
    reward: Reward | None
    value: Decimal
    amount: Decimal

    @property
    def effective_rate(self) -> Decimal:
        if not self.amount:
            return Decimal("0.0")
        return (self.value / self.amount * 100).quantize(Decimal("0.01"))

    @property
    def why(self) -> str:
        """The one-line justification, in the card's own terms."""
        if not self.reward:
            return "No rule on record for this"

        reward = self.reward
        where = reward.brand or (
            reward.get_category_display() if reward.category else "everything"
        )
        line = f"{reward.rate}% {reward.get_kind_display().lower()} on {where}"

        if reward.min_spend:
            line += f", min {reward.min_spend:.0f}"
        if reward.monthly_cap:
            line += f", capped at {reward.monthly_cap:.0f}/month"
        return line

    @property
    def caveat(self) -> str:
        """Anything that could make this advice wrong, said out loud."""
        if not self.reward:
            return ""
        if self.reward.min_spend and self.amount < self.reward.min_spend:
            return f"Below the {self.reward.min_spend:.0f} minimum, so it earns nothing"
        if self.reward.expires_soon:
            return f"This rate ends {self.reward.valid_to:%d %b}"
        return ""


def _applicable(reward: Reward, category: str, brand: str) -> bool:
    """Does this rule cover the purchase at hand?

    A blank category or brand on a rule means "anything", so a base-rate rule
    applies everywhere. A rule naming a brand only applies at that brand.
    """
    if reward.category and reward.category != category:
        return False
    if reward.brand and reward.brand.lower() != (brand or "").lower():
        return False
    return reward.is_current


def rank_cards(
    *,
    category: str = SpendCategory.OTHER,
    brand: str = "",
    amount: Decimal | None = None,
    cards=None,
) -> list[Pick]:
    """Every active card, best first, for one purchase.

    Cards with no applicable rule are still returned, scored at zero: knowing a
    card earns nothing here is as useful as knowing another earns 5%, and
    dropping it would leave you wondering whether it was considered.
    """
    amount = amount or DEFAULT_BASKET

    if cards is None:
        cards = Card.objects.filter(is_active=True).prefetch_related("rewards")

    picks: list[Pick] = []
    for card in cards:
        best_reward, best_value = None, Decimal("0.00")
        # A rule that covers this purchase but pays nothing - blocked by a
        # minimum spend, say - is still the reason the card scores zero. Held
        # separately so the card can explain itself instead of reporting "no
        # rule on record", which is a different and misleading answer.
        blocked_reward = None

        for reward in card.rewards.all():
            if not _applicable(reward, category, brand):
                continue

            value = reward.value_on(amount)
            if value == 0 and blocked_reward is None:
                blocked_reward = reward

            # Ties go to the more specific rule, so a brand deal beats a
            # category rate paying the same - it is the one that will still
            # apply if the category rate is restructured.
            more_specific = (
                best_reward is not None
                and value == best_value
                and bool(reward.brand) > bool(best_reward.brand)
            )
            if value > best_value or more_specific:
                best_reward, best_value = reward, value

        if best_reward is None:
            best_reward = blocked_reward

        picks.append(
            Pick(card=card, reward=best_reward, value=best_value, amount=amount)
        )

    picks.sort(key=lambda p: (-p.value, p.card.name))
    return picks


def best_for_place(place, amount: Decimal | None = None) -> list[Pick]:
    """Rank cards for a specific place on the map."""
    return rank_cards(
        category=category_for_place(place.kind),
        brand=place.brand,
        amount=amount,
    )


def coverage_gaps() -> list[dict]:
    """Categories where nothing in the wallet earns anything.

    Worth showing plainly: a gap is not a failure of the app, it is a fact
    about the wallet, and it is the most actionable thing on the screen.
    """
    rewards = list(Reward.objects.filter(card__is_active=True))
    current = [r for r in rewards if r.is_current]

    gaps = []
    for category in SpendCategory:
        covering = [
            r for r in current
            if not r.category or r.category == category.value
        ]
        best = max((r.rate for r in covering), default=Decimal("0"))
        gaps.append({
            "category": category.value,
            "label": category.label,
            "best_rate": best,
            "covered": bool(covering),
        })
    return gaps


def expiring_rewards(within_days: int = 30) -> list[Reward]:
    """Rules about to lapse, so the advice does not go stale silently."""
    from django.utils import timezone

    today = timezone.localdate()
    horizon = today + timezone.timedelta(days=within_days)
    return list(
        Reward.objects.filter(
            card__is_active=True,
            valid_to__isnull=False,
            valid_to__gte=today,
            valid_to__lte=horizon,
        )
        .select_related("card")
        .order_by("valid_to")
    )


def wallet_summary() -> dict:
    """Headline numbers for the overview screen."""
    cards = Card.objects.filter(is_active=True)
    rewards = Reward.objects.filter(card__is_active=True)
    current = [r for r in rewards if r.is_current]

    return {
        "cards": cards.count(),
        "rules": len(current),
        "best_rate": max((r.rate for r in current), default=Decimal("0")),
        "annual_fees": sum((c.annual_fee for c in cards), Decimal("0")),
        "gaps": [g for g in coverage_gaps() if not g["covered"]],
    }
