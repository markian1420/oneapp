"""
Credit cards and what they earn.

This is the one module that needs nothing from anybody else's website. The DOE
publishes fuel prices weekly, the DA publishes commodity prices daily, and
nobody publishes shop prices at all - but the rules on your own cards are
printed on your own statements. Type them in once and the app can answer
"which card do I tap here", which is a real question with a real peso answer,
every single time you pay for anything.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.categories import SpendCategory


class Card(models.Model):
    """One credit or debit card."""

    class Network(models.TextChoices):
        VISA = "visa", "Visa"
        MASTERCARD = "mastercard", "Mastercard"
        AMEX = "amex", "American Express"
        JCB = "jcb", "JCB"
        UNIONPAY = "unionpay", "UnionPay"
        OTHER = "other", "Other"

    name = models.CharField(max_length=80, help_text="What you call it.")
    issuer = models.CharField(max_length=80, blank=True, help_text="BPI, BDO, Metrobank…")
    network = models.CharField(
        max_length=20, choices=Network.choices, default=Network.VISA
    )
    last_four = models.CharField(
        max_length=4,
        blank=True,
        help_text="Last four digits only - never the full number.",
    )

    statement_day = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text="Day of the month the statement cuts.",
    )
    due_days_after_statement = models.PositiveSmallIntegerField(
        default=20,
        help_text="Days from statement date to payment due date.",
    )
    annual_fee = models.DecimalField(
        max_digits=9, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )

    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_active", "name"]

    def __str__(self) -> str:
        return self.label

    @property
    def label(self) -> str:
        parts = [self.issuer, self.name] if self.issuer else [self.name]
        label = " ".join(parts)
        return f"{label} ••{self.last_four}" if self.last_four else label

    @property
    def days_until_statement(self) -> int | None:
        """How long until this card's statement cuts.

        Useful because a purchase made just after a statement date gets the
        longest possible interest-free period, which is real money if you are
        carrying anything.
        """
        if not self.statement_day:
            return None

        today = timezone.localdate()
        day = min(self.statement_day, 28)
        if today.day <= day:
            return day - today.day

        # Next month's statement.
        if today.month == 12:
            nxt = today.replace(year=today.year + 1, month=1, day=day)
        else:
            nxt = today.replace(month=today.month + 1, day=day)
        return (nxt - today).days


class Reward(models.Model):
    """One earning rule on a card.

    A card usually has several: a headline rate on one category, a lower base
    rate on everything else, sometimes a brand-specific deal that beats both.
    Modelling them as rows rather than fields on the card is what lets the app
    pick the best applicable one rather than the only one there was room for.
    """

    class Kind(models.TextChoices):
        CASHBACK = "cashback", "Cashback"
        REBATE = "rebate", "Rebate"
        POINTS = "points", "Points"
        MILES = "miles", "Miles"
        DISCOUNT = "discount", "Discount"

    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="rewards")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.CASHBACK)

    category = models.CharField(
        max_length=20,
        choices=SpendCategory.choices,
        blank=True,
        help_text="Leave blank to earn on everything.",
    )
    brand = models.CharField(
        max_length=60,
        blank=True,
        help_text="Only at this brand, e.g. Shell. Blank means any.",
    )

    rate = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        validators=[MinValueValidator(0)],
        help_text="Percent back, or points per 100 pesos for a points card.",
    )
    peso_per_point = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("1"),
        validators=[MinValueValidator(0)],
        help_text="What one point is worth in pesos, so cards can be compared.",
    )

    monthly_cap = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text="Most this rule can earn in a month, in pesos. Blank = uncapped.",
    )
    min_spend = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text="Single-receipt minimum for this rule to apply.",
    )

    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(
        null=True, blank=True, help_text="Blank means ongoing."
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-rate", "card__name"]
        indexes = [
            models.Index(fields=["category", "brand"], name="idx_reward_lookup"),
        ]

    def __str__(self) -> str:
        where = self.brand or self.get_category_display() or "everything"
        return f"{self.card.name}: {self.rate}% on {where}"

    @property
    def is_current(self) -> bool:
        today = timezone.localdate()
        if self.valid_from and today < self.valid_from:
            return False
        if self.valid_to and today > self.valid_to:
            return False
        return True

    @property
    def expires_soon(self) -> bool:
        """Within a fortnight of lapsing.

        Worth surfacing: a promo rate quietly ending is the difference between
        the app's advice being right and being confidently wrong.
        """
        if not self.valid_to:
            return False
        remaining = (self.valid_to - timezone.localdate()).days
        return 0 <= remaining <= 14

    def value_on(self, amount: Decimal) -> Decimal:
        """What this rule returns on a given spend, in pesos.

        Points and miles are converted with peso_per_point so a 3-points card
        and a 2% cashback card can be put side by side. Comparing a percentage
        against a point count is how people end up choosing the worse card.
        """
        if self.min_spend and amount < self.min_spend:
            return Decimal("0.00")

        earned = amount * self.rate / Decimal(100)
        if self.kind in {self.Kind.POINTS, self.Kind.MILES}:
            earned = earned * self.peso_per_point

        if self.monthly_cap is not None:
            earned = min(earned, self.monthly_cap)
        return earned.quantize(Decimal("0.01"))
