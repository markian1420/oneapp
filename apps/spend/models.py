"""
What you actually spent, and the offers that were running when you spent it.

Fuel already has FillUp, which is richer than anything here because litres and
odometer readings mean something specific. Everything else - a grocery basket,
a dinner, a shirt - is the same shape: a place, a date, an amount, a card, and
optionally the things in it. One model rather than four keeps the budget and
the insight layer from needing a union query to answer "what did I spend".

Promos live here too rather than in their own app, because a promo only earns
its keep by being attached to spending: whether it actually saved you anything
is a question about a purchase.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.categories import SpendCategory
from apps.places.models import Place


class Purchase(models.Model):
    """One transaction, in any category except fuel."""

    place = models.ForeignKey(
        Place, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="purchases",
        help_text="Where. Optional - not everything happens somewhere mapped.",
    )
    category = models.CharField(
        max_length=20, choices=SpendCategory.choices, db_index=True
    )
    merchant = models.CharField(
        max_length=120, blank=True,
        help_text="Used when the place is not on the map.",
    )

    occurred_at = models.DateTimeField(default=timezone.now)
    total = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )

    # Free text rather than a link to a stored card. The app deliberately
    # holds no record of anybody's cards - a note saying "BPI" is enough to
    # group spending by payment method, and it is not card data.
    paid_with = models.CharField(
        max_length=60, blank=True,
        help_text='How you paid, e.g. "BPI credit" or "cash". Never card details.',
    )
    reward_earned = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text="Cashback or points value, if you know it.",
    )

    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["-occurred_at"], name="idx_purchase_time"),
            models.Index(fields=["category", "-occurred_at"],
                         name="idx_purchase_category"),
        ]
        ordering = ["-occurred_at"]

    def __str__(self) -> str:
        return f"{self.occurred_at:%Y-%m-%d} {self.where} {self.total}"

    @property
    def where(self) -> str:
        if self.place:
            return self.place.display_name
        return self.merchant or "Unrecorded"

    @property
    def item_total(self) -> Decimal:
        return sum((i.amount for i in self.items.all()), Decimal("0.00"))

    @property
    def items_disagree(self) -> bool:
        """Do the lines add up to the receipt total?

        Surfaced rather than silently corrected: a mismatch usually means a
        line was missed, and quietly rewriting the total would hide it.
        """
        if not self.items.exists():
            return False
        return abs(self.item_total - self.total) > Decimal("1.00")


class PurchaseItem(models.Model):
    """A line on a receipt.

    Optional everywhere, essential for two things: comparing what a basket
    costs between shops, and cost-per-wear on clothing.
    """

    purchase = models.ForeignKey(
        Purchase, on_delete=models.CASCADE, related_name="items"
    )
    description = models.CharField(max_length=160)
    quantity = models.DecimalField(
        max_digits=8, decimal_places=3, default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_price = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    amount = models.DecimalField(
        max_digits=9, decimal_places=2, validators=[MinValueValidator(0)]
    )

    # ---- Clothing only -------------------------------------------------
    # Apparel is the one category where the useful number is not the price.
    # A 3,000 peso jacket worn 100 times costs 30 a wear; a 600 peso shirt
    # worn twice costs 300. Cost per wear is the apparel equivalent of pesos
    # per litre, and it is the figure that actually changes buying habits.
    is_wearable = models.BooleanField(
        default=False, help_text="Track how often this gets worn."
    )
    wears = models.PositiveIntegerField(default=0)
    retired_on = models.DateField(
        null=True, blank=True, help_text="When you stopped wearing it."
    )

    class Meta:
        ordering = ["pk"]

    def __str__(self) -> str:
        return self.description

    @property
    def cost_per_wear(self) -> Decimal | None:
        if not self.is_wearable or not self.wears:
            return None
        return (self.amount / self.wears).quantize(Decimal("0.01"))

    @property
    def verdict(self) -> str:
        """Plain language on whether it earned its price.

        Thresholds are deliberately round numbers rather than anything
        derived: this is a nudge, not a measurement, and pretending to
        precision here would be false.
        """
        cost = self.cost_per_wear
        if cost is None:
            return "Not worn yet" if self.is_wearable else ""
        if cost <= Decimal("50"):
            return "Earned its price"
        if cost <= Decimal("200"):
            return "Getting there"
        return "Expensive per wear so far"


class Promo(models.Model):
    """An offer that runs until it does not.

    No Philippine chain publishes promos in any machine-readable form - the
    brand pages are images with the terms baked into the graphic, and no
    validity dates in text. So these are entered by hand, and the field that
    matters most is the one nobody publishes: when it ends. A promo tracker
    that shows expired offers is worse than no tracker.
    """

    title = models.CharField(max_length=160)
    brand = models.CharField(
        max_length=60, blank=True, db_index=True,
        help_text="Jollibee, 7-Eleven, Uniqlo… Blank for a store-wide sale.",
    )

    # A card promo needs a card to qualify; a merchant promo is open to
    # everyone. Both are offers with an expiry, so they share this model
    # rather than duplicating the whole shape - the issuer is what tells them
    # apart, and it is a fact about the offer, not about you.
    issuer = models.CharField(
        max_length=60, blank=True, db_index=True,
        help_text="BPI, BDO, Metrobank… Blank means anyone can use it.",
    )
    card_name = models.CharField(
        max_length=120, blank=True,
        help_text='Which card qualifies, e.g. "Gold Rewards". Blank means any '
                  "card from that issuer.",
    )
    category = models.CharField(
        max_length=20, choices=SpendCategory.choices, db_index=True
    )

    detail = models.CharField(max_length=250, blank=True)
    discount_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    price = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text="For a fixed-price deal, e.g. a 99 peso meal.",
    )
    min_spend = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )

    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(
        null=True, blank=True,
        help_text="Leave blank only if it genuinely has no end date.",
    )

    source_url = models.URLField(blank=True)
    source_note = models.CharField(
        max_length=160, blank=True, help_text="Where you saw it."
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ends_on", "-added_at"]
        indexes = [
            models.Index(fields=["category", "ends_on"], name="idx_promo_lookup"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_live(self) -> bool:
        today = timezone.localdate()
        if self.starts_on and today < self.starts_on:
            return False
        if self.ends_on and today > self.ends_on:
            return False
        return True

    @property
    def days_left(self) -> int | None:
        if not self.ends_on:
            return None
        return (self.ends_on - timezone.localdate()).days

    @property
    def status(self) -> str:
        today = timezone.localdate()
        if self.starts_on and today < self.starts_on:
            return "upcoming"
        if self.ends_on and today > self.ends_on:
            return "expired"
        if self.days_left is not None and self.days_left <= 3:
            return "ending"
        return "live"

    @property
    def badge_class(self) -> str:
        return {
            "live": "badge-success",
            "ending": "badge-warning",
            "upcoming": "badge-info",
            "expired": "badge-muted",
        }[self.status]

    @property
    def is_card_promo(self) -> bool:
        return bool(self.issuer)

    @property
    def qualifies(self) -> str:
        """Which cards can use this, in one line."""
        if not self.issuer:
            return "Any payment"
        return f"{self.issuer} {self.card_name}".strip() if self.card_name else (
            f"Any {self.issuer} card"
        )

    @property
    def undated(self) -> bool:
        """No end date at all.

        Worth calling out on screen. Brands rarely publish one, so most entries
        will be undated, and an undated promo is the one most likely to have
        quietly ended.
        """
        return self.ends_on is None
