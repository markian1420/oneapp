"""Forms for logging spend and recording offers."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.fuel.forms import DateTimeLocalInput
from apps.places.models import Place

from .models import Promo, Purchase, PurchaseItem


class PurchaseForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = [
            "place", "merchant", "category", "occurred_at",
            "total", "paid_with", "reward_earned", "notes",
        ]
        widgets = {
            "occurred_at": DateTimeLocalInput(),
            "notes": forms.TextInput(attrs={"placeholder": "Optional"}),
        }
        labels = {
            "place": "Where (on the map)",
            "merchant": "Or type a name",
            "total": "Total paid",
            "paid_with": "Paid with",
            "reward_earned": "Reward earned",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["place"].queryset = Place.objects.order_by("-is_favorite", "name")
        self.fields["place"].required = False
        if not self.instance.pk:
            self.fields["occurred_at"].initial = timezone.localtime()

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("place") and not cleaned.get("merchant"):
            raise forms.ValidationError(
                "Say where: pick a place from the map, or type a name."
            )
        return cleaned


class ItemForm(forms.ModelForm):
    """One line on a receipt."""

    class Meta:
        model = PurchaseItem
        fields = ["description", "quantity", "unit_price", "amount", "is_wearable"]
        labels = {
            "unit_price": "Unit price",
            "amount": "Line total",
            "is_wearable": "Clothing - track wears",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].required = False
        self.fields["unit_price"].required = False

    def clean(self):
        cleaned = super().clean()
        quantity = cleaned.get("quantity")
        unit = cleaned.get("unit_price")
        amount = cleaned.get("amount")

        # Same contract as a fill-up: give what the receipt shows and let the
        # app work out the rest, rather than making someone do arithmetic in
        # a queue.
        if amount is None and unit is not None and quantity:
            cleaned["amount"] = (unit * quantity).quantize(Decimal("0.01"))
        elif unit is None and amount is not None and quantity:
            cleaned["unit_price"] = (amount / quantity).quantize(Decimal("0.01"))

        if cleaned.get("amount") is None:
            raise forms.ValidationError(
                "Give a line total, or a unit price and a quantity."
            )
        return cleaned


class PromoForm(forms.ModelForm):
    class Meta:
        model = Promo
        fields = [
            "title", "brand", "issuer", "card_name", "category", "detail",
            "discount_pct", "price", "min_spend",
            "starts_on", "ends_on", "source_url", "source_note",
        ]
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "discount_pct": "Discount (%)",
            "price": "Fixed price (₱)",
            "min_spend": "Minimum spend (₱)",
            "starts_on": "Starts",
            "ends_on": "Ends",
            "source_note": "Where you saw it",
            "issuer": "Bank (for a card promo)",
            "card_name": "Which card",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        brands = (
            Place.objects.exclude(brand="")
            .values_list("brand", flat=True).order_by("brand").distinct()
        )
        self.fields["brand"] = forms.ChoiceField(
            required=False,
            choices=[("", "Any brand")] + [(b, b) for b in brands],
        )
        self.fields["ends_on"].help_text = (
            "The most important field here. Brands rarely publish it, so if the "
            "poster does not say, put your best guess rather than nothing."
        )

    def clean(self):
        cleaned = super().clean()
        starts, ends = cleaned.get("starts_on"), cleaned.get("ends_on")
        if starts and ends and ends < starts:
            self.add_error("ends_on", "The end date is before the start date.")
        if not cleaned.get("discount_pct") and not cleaned.get("price"):
            self.add_error(
                "discount_pct", "Give a discount or a fixed price, or it says nothing."
            )
        return cleaned
