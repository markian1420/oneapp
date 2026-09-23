"""
Fuel domain: stations, the prices we know for them, and what we actually paid.

Price provenance is modelled explicitly rather than flattened into one number.
There is no live per-station price feed in the Philippines - the DOE publishes a
weekly advisory by brand and region, and everything else on the market is either
that advisory or someone typing in what they saw. So a price carries the tier it
came from, and the UI says which. A guess presented as a fact is worse than no
price at all when the decision is "is this detour worth it".
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.places.models import Place


class FuelType(models.TextChoices):
    """Grades sold at Philippine pumps.

    RON 91/95/97 rather than "regular/premium": the brands disagree on what
    those words mean, but the octane number is on the pump everywhere.
    """

    GAS_91 = "gas_91", "Gasoline RON 91"
    GAS_95 = "gas_95", "Gasoline RON 95"
    GAS_97 = "gas_97", "Gasoline RON 97"
    DIESEL = "diesel", "Diesel"
    DIESEL_PREMIUM = "diesel_premium", "Premium diesel"


class PriceTier(models.TextChoices):
    """How much a price is worth trusting, best first."""

    LOGGED = "logged", "You paid this"
    SURVEY = "survey", "Station survey"
    ADVISORY = "advisory", "DOE weekly advisory"
    ESTIMATED = "estimated", "Regional price"
    UNKNOWN = "unknown", "No price"


class PriceObservation(models.Model):
    """One price seen at one station, at one moment.

    Written both by fill-up logging (the trustworthy path - you have the
    receipt) and by manually noting a price board without buying.
    """

    class Source(models.TextChoices):
        FILL_UP = "fill_up", "From a fill-up"
        SPOTTED = "spotted", "Price board"

    place = models.ForeignKey(
        Place, on_delete=models.CASCADE, related_name="fuel_observations"
    )
    fuel_type = models.CharField(max_length=20, choices=FuelType.choices)
    price = models.DecimalField(
        max_digits=7,
        decimal_places=3,
        validators=[MinValueValidator(0)],
        help_text="Pesos per litre.",
    )
    observed_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(
        max_length=12, choices=Source.choices, default=Source.SPOTTED
    )
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        indexes = [
            # "Latest price for this station and grade" is the single hottest
            # read in the app - it runs once per marker on the map.
            models.Index(
                fields=["place", "fuel_type", "-observed_at"],
                name="idx_obs_station_fuel_time",
            ),
        ]
        ordering = ["-observed_at"]

    def __str__(self) -> str:
        return f"{self.place} {self.get_fuel_type_display()} @ {self.price}"

    @property
    def is_fresh(self) -> bool:
        return (timezone.now() - self.observed_at).days <= settings.PRICE_FRESH_DAYS


class StationSurveyPrice(models.Model):
    """Somebody else's price for one station and grade.

    Kept apart from PriceObservation on purpose. That table means "seen
    first-hand" - a receipt or a price board someone read - and counting a
    survey among those would overstate how much of the map anyone has actually
    looked at, in the one place the app promises not to.

    One row per station and grade, replaced where it stands. A survey has no
    history worth keeping: the publisher overwrites its own numbers, so
    accumulating every version here would grow the table by thousands of rows
    a week to preserve a record nobody can cite.
    """

    place = models.ForeignKey(
        Place, on_delete=models.CASCADE, related_name="fuel_survey_prices"
    )
    fuel_type = models.CharField(max_length=20, choices=FuelType.choices)
    price = models.DecimalField(
        max_digits=7,
        decimal_places=3,
        validators=[MinValueValidator(0)],
        help_text="Pesos per litre.",
    )
    as_of = models.DateField(help_text="The date the survey itself carries.")
    source_name = models.CharField(max_length=80)
    source_url = models.URLField(blank=True)
    # What the survey calls this station, which is rarely what OSM calls it.
    # Worth keeping: it is the only way to check a match by eye afterwards.
    station_label = models.CharField(max_length=200, blank=True)
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["place", "fuel_type"], name="uniq_survey_station_fuel"
            ),
        ]
        indexes = [
            models.Index(
                fields=["fuel_type", "place"], name="idx_survey_fuel_station"
            ),
        ]
        ordering = ["place_id", "fuel_type"]

    def __str__(self) -> str:
        return f"{self.place} {self.get_fuel_type_display()} @ {self.price} (survey)"

    @property
    def is_fresh(self) -> bool:
        return (timezone.localdate() - self.as_of).days <= settings.PRICE_FRESH_DAYS


class DOEAdvisory(models.Model):
    """A row from the DOE weekly retail price advisory.

    The DOE publishes per brand and per region, not per station, and it is a
    spreadsheet rather than an API - so these rows are loaded by the importer
    or typed in, and are the baseline for every station you have never visited.
    A blank brand is the prevailing price across brands in that region.
    """

    week_of = models.DateField(help_text="Monday of the week the advisory covers.")
    region = models.CharField(max_length=40, db_index=True)
    brand = models.CharField(
        max_length=60, blank=True, help_text="Blank means prevailing across brands."
    )
    fuel_type = models.CharField(max_length=20, choices=FuelType.choices)
    price = models.DecimalField(
        max_digits=7, decimal_places=3, validators=[MinValueValidator(0)]
    )

    # A market survey reports a range, not a single number. The DOE bulletin
    # gives one figure per brand; a survey of 1,292 pumps gives a median and a
    # spread, and the spread is the more interesting half - it is the reason
    # driving past one station to another is worth anything.
    low = models.DecimalField(
        max_digits=7, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text="Cheapest price seen in this region, where the source reports one.",
    )
    high = models.DecimalField(
        max_digits=7, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    sample_size = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="How many stations the figure was drawn from.",
    )

    source_url = models.URLField(blank=True)
    source_note = models.CharField(max_length=200, blank=True)
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "DOE advisory"
        verbose_name_plural = "DOE advisories"
        constraints = [
            models.UniqueConstraint(
                fields=["week_of", "region", "brand", "fuel_type"],
                name="uniq_advisory_week_region_brand_fuel",
            )
        ]
        indexes = [
            models.Index(
                fields=["region", "fuel_type", "-week_of"],
                name="idx_advisory_lookup",
            )
        ]
        ordering = ["-week_of", "region", "brand"]

    def __str__(self) -> str:
        brand = self.brand or "prevailing"
        return f"{self.week_of} {self.region} {brand} {self.fuel_type} @ {self.price}"

    @property
    def spread(self):
        """The gap between the cheapest and dearest pump surveyed."""
        if self.low is None or self.high is None:
            return None
        return self.high - self.low

    @property
    def spread_on_a_tank(self):
        """What that gap is worth on a 40-litre fill, in pesos.

        A per-litre spread is easy to shrug at. The same number multiplied by a
        tank is what makes the case for driving past one station to another.
        """
        gap = self.spread
        return (gap * 40).quantize(Decimal("0.01")) if gap is not None else None


class Vehicle(models.Model):
    """What you drive - needed to turn a peso-per-litre gap into pesos saved.

    Without consumption and tank size, "2 pesos cheaper" is not a decision: the
    answer depends on how many litres you are buying and how far you drove to
    buy them.
    """

    name = models.CharField(max_length=80)
    plate = models.CharField(max_length=20, blank=True)
    default_fuel_type = models.CharField(
        max_length=20, choices=FuelType.choices, default=FuelType.GAS_95
    )
    tank_capacity_l = models.DecimalField(
        max_digits=5, decimal_places=1, default=40, validators=[MinValueValidator(1)]
    )
    km_per_liter = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=10,
        validators=[MinValueValidator(0.1)],
        help_text="Real-world average. The fill-up log refines this over time.",
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Vehicle.objects.exclude(pk=self.pk).filter(is_default=True).update(
                is_default=False
            )


class FillUp(models.Model):
    """A tank of fuel actually bought.

    Doubles as the app's best price data: unlike anything scraped, this is a
    price someone stood in front of and paid. Saving one writes a matching
    PriceObservation.
    """

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="fill_ups"
    )
    place = models.ForeignKey(
        Place, on_delete=models.PROTECT, related_name="fill_ups"
    )
    fuel_type = models.CharField(max_length=20, choices=FuelType.choices)

    filled_at = models.DateTimeField(default=timezone.now)
    liters = models.DecimalField(
        max_digits=7, decimal_places=3, validators=[MinValueValidator(0.001)]
    )
    price_per_liter = models.DecimalField(
        max_digits=7, decimal_places=3, validators=[MinValueValidator(0)]
    )
    total_cost = models.DecimalField(
        max_digits=9, decimal_places=2, validators=[MinValueValidator(0)]
    )

    odometer_km = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional. Two readings in a row give real fuel economy.",
    )
    is_full_tank = models.BooleanField(
        default=True,
        help_text="Only full-to-full pairs give a valid economy figure.",
    )
    notes = models.CharField(max_length=200, blank=True)

    observation = models.OneToOneField(
        PriceObservation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fill_up",
        editable=False,
    )

    class Meta:
        indexes = [
            models.Index(fields=["-filled_at"], name="idx_fillup_time"),
            models.Index(fields=["place", "-filled_at"], name="idx_fillup_place"),
        ]
        ordering = ["-filled_at"]

    def __str__(self) -> str:
        return f"{self.filled_at:%Y-%m-%d} {self.place} {self.liters}L"

    def sync_observation(self) -> PriceObservation:
        """Mirror this fill-up into the price layer.

        An explicit call from the form rather than a save() override: the
        importer and the tests both create fill-ups, and a hidden write into
        another table is the kind of thing that surprises you later.
        """
        values = {
            "place": self.place,
            "fuel_type": self.fuel_type,
            "price": self.price_per_liter,
            "observed_at": self.filled_at,
            "source": PriceObservation.Source.FILL_UP,
        }
        if self.observation_id:
            for key, value in values.items():
                setattr(self.observation, key, value)
            self.observation.save()
        else:
            self.observation = PriceObservation.objects.create(**values)
            FillUp.objects.filter(pk=self.pk).update(observation=self.observation)
        return self.observation
