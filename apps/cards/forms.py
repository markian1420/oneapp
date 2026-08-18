"""Forms for describing what your cards actually earn."""

from __future__ import annotations

from django import forms

from apps.places.models import Place

from .models import Card, Reward


class CardForm(forms.ModelForm):
    class Meta:
        model = Card
        fields = [
            "name", "issuer", "network", "last_four",
            "statement_day", "due_days_after_statement",
            "annual_fee", "is_active", "notes",
        ]
        labels = {
            "last_four": "Last 4 digits",
            "statement_day": "Statement day",
            "due_days_after_statement": "Days until due",
            "annual_fee": "Annual fee",
            "is_active": "Still using this card",
        }
        help_texts = {
            # Said plainly on the form, not just in a policy somewhere: the app
            # never needs the full number, so it never asks for it.
            "last_four": "For telling cards apart. Never enter the full number.",
        }

    def clean_last_four(self):
        value = self.cleaned_data["last_four"].strip()
        if value and (not value.isdigit() or len(value) != 4):
            raise forms.ValidationError("Four digits, or leave it blank.")
        return value


class RewardForm(forms.ModelForm):
    """One earning rule.

    The brand field offers what is actually on the map, so a rule typed as
    "Shell" matches the 251 Shell stations already imported rather than sitting
    there never matching anything.
    """

    class Meta:
        model = Reward
        fields = [
            "kind", "category", "brand", "rate", "peso_per_point",
            "monthly_cap", "min_spend", "valid_from", "valid_to", "notes",
        ]
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_to": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "rate": "Rate (%)",
            "peso_per_point": "Peso value per point",
            "monthly_cap": "Monthly cap (₱)",
            "min_spend": "Minimum spend (₱)",
            "valid_from": "Starts",
            "valid_to": "Ends",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        brands = (
            Place.objects.exclude(brand="")
            .values_list("brand", flat=True)
            .order_by("brand")
            .distinct()
        )
        self.fields["brand"] = forms.ChoiceField(
            required=False,
            choices=[("", "Any brand")] + [(b, b) for b in brands],
            help_text="Only for a deal tied to one chain.",
        )

        # Only points and miles need a conversion; showing it for a cashback
        # card invites someone to change a number that does nothing.
        self.fields["peso_per_point"].help_text = (
            "Only used for points and miles cards."
        )

    def clean(self):
        cleaned = super().clean()
        starts, ends = cleaned.get("valid_from"), cleaned.get("valid_to")
        if starts and ends and ends < starts:
            self.add_error("valid_to", "The end date is before the start date.")

        kind = cleaned.get("kind")
        if kind in {Reward.Kind.POINTS, Reward.Kind.MILES}:
            if not cleaned.get("peso_per_point"):
                self.add_error(
                    "peso_per_point",
                    "Needed to compare a points card against a cashback one.",
                )
        return cleaned


class WhichCardForm(forms.Form):
    """The question you ask at the counter."""

    from apps.core.categories import SpendCategory

    category = forms.ChoiceField(
        choices=SpendCategory.choices, required=False, label="Spending on"
    )
    brand = forms.CharField(required=False, label="Brand")
    amount = forms.DecimalField(
        required=False, min_value=0, decimal_places=2, max_digits=9,
        label="Amount (₱)",
    )
