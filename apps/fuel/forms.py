"""Forms for logging what you paid and what the DOE said."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.utils import timezone

from .models import DOEAdvisory, FillUp, FuelType, PriceObservation, Station, Vehicle
from .regions import REGION_NAMES
from .services import week_start


class DateTimeLocalInput(forms.DateTimeInput):
    """A real datetime picker.

    Django's default renders a text box and then rejects the browser's own
    format, which on a phone is the difference between two taps and typing a
    timestamp by hand at a petrol station.
    """

    input_type = "datetime-local"

    def format_value(self, value):
        """Render as the exact string the input expects.

        Django's DateTimeField.prepare_value has already converted an aware
        value to naive local time, so this must not convert again - doing so
        raises on the naive datetime it is handed. An aware value can still
        arrive from a caller that bypasses the field, hence the guard.
        """
        if value in (None, ""):
            return ""
        if isinstance(value, str):
            return value
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime("%Y-%m-%dT%H:%M")


class FillUpForm(forms.ModelForm):
    """Log a tank.

    Litres, price per litre and total are related by one multiplication, and a
    receipt shows all three - but people remember different pairs, so any two
    are enough and the third is worked out. When all three are given and they
    disagree, that is a typo worth stopping on rather than silently rounding
    away.
    """

    class Meta:
        model = FillUp
        fields = [
            "vehicle", "station", "fuel_type", "filled_at",
            "liters", "price_per_liter", "total_cost",
            "odometer_km", "is_full_tank", "notes",
        ]
        widgets = {
            "filled_at": DateTimeLocalInput(),
            "notes": forms.TextInput(attrs={"placeholder": "Optional"}),
        }
        labels = {
            "liters": "Litres",
            "price_per_liter": "Price per litre",
            "total_cost": "Total paid",
            "odometer_km": "Odometer (km)",
            "is_full_tank": "Filled to full",
        }
        help_texts = {
            "liters": "Leave blank to work it out from the total and the price.",
            "price_per_liter": "Leave blank to work it out from the total and litres.",
            "total_cost": "Leave blank to work it out from litres and the price.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Any two of the three are enough, so none of them can be required at
        # the field level; clean() enforces the real rule.
        for name in ("liters", "price_per_liter", "total_cost"):
            self.fields[name].required = False

        self.fields["station"].queryset = Station.objects.order_by(
            "-is_favorite", "name"
        )
        self.fields["vehicle"].queryset = Vehicle.objects.all()

        if not self.instance.pk:
            self.fields["filled_at"].initial = timezone.localtime()
            default_vehicle = Vehicle.objects.filter(is_default=True).first()
            if default_vehicle:
                self.fields["vehicle"].initial = default_vehicle
                self.fields["fuel_type"].initial = default_vehicle.default_fuel_type

    def clean(self):
        cleaned = super().clean()
        liters = cleaned.get("liters")
        price = cleaned.get("price_per_liter")
        total = cleaned.get("total_cost")

        given = [value for value in (liters, price, total) if value is not None]
        if len(given) < 2:
            raise forms.ValidationError(
                "Give at least two of litres, price per litre and total paid - "
                "the third is worked out for you."
            )

        try:
            if liters is None:
                cleaned["liters"] = (total / price).quantize(Decimal("0.001"))
            elif price is None:
                cleaned["price_per_liter"] = (total / liters).quantize(Decimal("0.001"))
            elif total is None:
                cleaned["total_cost"] = (liters * price).quantize(Decimal("0.01"))
            else:
                expected = liters * price
                # One peso of slack: pumps round the display, and a receipt
                # rounds again. More than that is a mistyped figure.
                if abs(expected - total) > Decimal("1.00"):
                    self.add_error(
                        "total_cost",
                        f"{liters} L at {price}/L comes to {expected.quantize(Decimal('0.01'))}, "
                        f"not {total}. Check the receipt.",
                    )
        except (InvalidOperation, ZeroDivisionError):
            raise forms.ValidationError(
                "Those numbers do not divide - check for a zero."
            ) from None

        return cleaned

    def save(self, commit=True):
        fill_up = super().save(commit=commit)
        if commit:
            fill_up.sync_observation()
        return fill_up


class PriceReportForm(forms.ModelForm):
    """Note a price board without buying anything."""

    class Meta:
        model = PriceObservation
        fields = ["fuel_type", "price", "observed_at", "note"]
        widgets = {
            "observed_at": DateTimeLocalInput(),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }
        labels = {"price": "Price per litre"}

    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.station = station
        if not self.instance.pk:
            self.fields["observed_at"].initial = timezone.localtime()

    def save(self, commit=True):
        observation = super().save(commit=False)
        observation.station = self.station
        observation.source = PriceObservation.Source.SPOTTED
        if commit:
            observation.save()
        return observation


class AdvisoryEntryForm(forms.Form):
    """Type in one week's advisory for a region.

    The DOE publishes a handful of numbers per region once a week. Entering
    them takes a minute and is the only path that does not depend on somebody
    else's website staying up, so it is a first-class screen rather than a
    fallback buried in a management command.
    """

    week_of = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Any day in the week; it is stored against that Monday.",
    )
    region = forms.ChoiceField(
        choices=[(code, f"{code} - {name}") for code, name in REGION_NAMES.items()]
    )
    brand = forms.CharField(
        required=False,
        help_text="Leave blank for the region's prevailing price across brands.",
    )
    source_url = forms.URLField(
        required=False, label="Source", help_text="The DOE page or bulletin you read."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["week_of"].initial = week_start()
        for fuel_type in FuelType:
            self.fields[f"price_{fuel_type.value}"] = forms.DecimalField(
                required=False,
                max_digits=7,
                decimal_places=3,
                min_value=Decimal("0.001"),
                label=fuel_type.label,
            )

    @property
    def price_fields(self):
        """The per-grade inputs, for rendering them as one block."""
        return [self[f"price_{fuel_type.value}"] for fuel_type in FuelType]

    def clean(self):
        cleaned = super().clean()
        if not any(
            cleaned.get(f"price_{fuel_type.value}") is not None
            for fuel_type in FuelType
        ):
            raise forms.ValidationError("Enter a price for at least one grade.")
        return cleaned

    def save(self) -> int:
        week = week_start(self.cleaned_data["week_of"])
        region = self.cleaned_data["region"]
        brand = self.cleaned_data["brand"].strip()
        written = 0

        for fuel_type in FuelType:
            price = self.cleaned_data.get(f"price_{fuel_type.value}")
            if price is None:
                continue
            DOEAdvisory.objects.update_or_create(
                week_of=week,
                region=region,
                brand=brand,
                fuel_type=fuel_type.value,
                defaults={
                    "price": price,
                    "source_url": self.cleaned_data.get("source_url", ""),
                    "source_note": "Entered by hand",
                    "fetched_at": timezone.now(),
                },
            )
            written += 1
        return written


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            "name", "plate", "default_fuel_type",
            "tank_capacity_l", "km_per_liter", "is_default",
        ]
        labels = {
            "tank_capacity_l": "Tank capacity (L)",
            "km_per_liter": "Fuel economy (km/L)",
            "is_default": "Use this vehicle by default",
        }
