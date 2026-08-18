"""
Grocery domain: what things cost, from the sources that actually publish.

The price situation here is the mirror image of fuel. Fuel has no official
per-station feed at all; groceries have a good one - the DA publishes a Daily
Price Index for NCR covering ~160 agri-fishery commodities across 33 named wet
markets - but it stops at commodities. No supermarket publishes shelf prices,
so a specific brand of shampoo at a specific Puregold is still something only a
receipt can tell you.

So the same provenance discipline as fuel applies, for the same reason: a price
carries where it came from, and the UI says so.
"""

from __future__ import annotations

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class CommodityCategory(models.TextChoices):
    """The section headings the DA Daily Price Index is organised by."""

    IMPORTED_RICE = "imported_rice", "Imported commercial rice"
    LOCAL_RICE = "local_rice", "Local commercial rice"
    CORN = "corn", "Corn products"
    LEGUMES = "legumes", "Legumes"
    FISH = "fish", "Fish products"
    BEEF = "beef", "Beef"
    PORK = "pork", "Pork"
    OTHER_MEAT = "other_meat", "Other livestock meat"
    POULTRY = "poultry", "Poultry"
    LOWLAND_VEG = "lowland_veg", "Lowland vegetables"
    HIGHLAND_VEG = "highland_veg", "Highland vegetables"
    SPICES = "spices", "Spices"
    FRUITS = "fruits", "Fruits"
    OTHER_BASIC = "other_basic", "Other basic commodities"
    PACKAGED = "packaged", "Packaged goods"
    OTHER = "other", "Other"


class PriceSource(models.TextChoices):
    """Where a grocery price came from, best first."""

    RECEIPT = "receipt", "You paid this"
    DA_DAILY = "da_daily", "DA daily price index"
    DTI_SRP = "dti_srp", "DTI suggested retail price"
    SPOTTED = "spotted", "Shelf price seen"


class Commodity(models.Model):
    """One tracked item.

    Identity is (category, name, specification) because the DA index
    distinguishes "Chicken Breast, Local Magnolia" from "Chicken Breast, Local
    Unbranded, Fresh" and the price gap between them is the entire point.
    """

    category = models.CharField(
        max_length=20,
        choices=CommodityCategory.choices,
        default=CommodityCategory.OTHER,
        db_index=True,
    )
    name = models.CharField(max_length=160)
    specification = models.CharField(
        max_length=160,
        blank=True,
        help_text='Grade, size or brand, e.g. "Medium (4-6 pcs/kg)".',
    )
    unit = models.CharField(max_length=20, default="kg")

    is_tracked = models.BooleanField(
        default=False,
        help_text="On your watchlist - these get charted and alerted on.",
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "commodities"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "name", "specification"],
                name="uniq_commodity_identity",
            )
        ]
        indexes = [models.Index(fields=["name"], name="idx_commodity_name")]
        ordering = ["category", "name", "specification"]

    def __str__(self) -> str:
        return self.label

    @property
    def label(self) -> str:
        if self.specification:
            return f"{self.name} ({self.specification})"
        return self.name


class CommodityPrice(models.Model):
    """A price for one commodity on one day, from one source.

    The DA publishes a single prevailing price per commodity for the whole of
    NCR rather than per market, so this is regional by design. A receipt price
    is tied to a place; that lives on the basket line instead, because a price
    with a shop attached is a different, better thing than this.
    """

    commodity = models.ForeignKey(
        Commodity, on_delete=models.CASCADE, related_name="prices"
    )
    region = models.CharField(max_length=40, default="NCR", db_index=True)
    observed_on = models.DateField()
    price = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Pesos per unit.",
    )
    source = models.CharField(
        max_length=12, choices=PriceSource.choices, default=PriceSource.DA_DAILY
    )
    source_url = models.URLField(blank=True)
    imported_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["commodity", "region", "observed_on", "source"],
                name="uniq_commodity_price_per_day",
            )
        ]
        indexes = [
            # "The series for this commodity" is what every chart, trend and
            # anomaly check reads, so it is the index that matters.
            models.Index(
                fields=["commodity", "region", "-observed_on"],
                name="idx_price_series",
            ),
            models.Index(fields=["-observed_on"], name="idx_price_day"),
        ]
        ordering = ["-observed_on", "commodity__name"]

    def __str__(self) -> str:
        return f"{self.observed_on} {self.commodity} @ {self.price}"
