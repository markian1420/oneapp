"""
Tests for the briefing.

The important property is not that it produces insights - it is that it stays
quiet when the evidence is thin, and says how thin. An app that confidently
reports a pattern from two data points teaches you to ignore it.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.categories import SpendCategory
from apps.fuel.models import FillUp, Vehicle
from apps.grocery.models import Commodity, CommodityCategory, CommodityPrice
from apps.places.models import Place, PlaceKind
from apps.spend.models import Promo, Purchase, PurchaseItem

from .services import (
    REQUIREMENTS,
    build_briefing,
    card_promo_coverage,
    fuel_rhythm,
    grocery_swing,
    idle_wardrobe,
    promo_watch,
)


def make_place(osm_id=1, **overrides) -> Place:
    values = {
        "kind": PlaceKind.FUEL, "osm_type": Place.OSMType.NODE, "osm_id": osm_id,
        "name": "Shell Ortigas", "brand": "Shell",
        "latitude": Decimal("14.58"), "longitude": Decimal("121.06"),
        "region": "NCR",
    }
    values.update(overrides)
    return Place.objects.create(**values)


class ConfidenceTests(TestCase):
    def setUp(self):
        self.vehicle = Vehicle.objects.create(name="Car", is_default=True)
        self.place = make_place()

    def _fill(self, days_ago, price="78.00"):
        return FillUp.objects.create(
            vehicle=self.vehicle, place=self.place, fuel_type="gas_95",
            filled_at=timezone.now() - timedelta(days=days_ago),
            liters=Decimal("40"), price_per_liter=Decimal(price),
            total_cost=Decimal("40") * Decimal(price),
        )

    def test_two_fill_ups_are_not_a_rhythm(self):
        self._fill(30)
        self._fill(15)
        self.assertIsNone(fuel_rhythm())

    def test_a_regular_pattern_gives_a_due_date(self):
        for days in (60, 45, 30, 15):
            self._fill(days)

        insight = fuel_rhythm()
        self.assertIsNotNone(insight)
        self.assertIn("15 days", insight.detail)

    def test_an_overdue_fill_up_is_flagged(self):
        for days in (90, 75, 60):
            self._fill(days)

        insight = fuel_rhythm()
        self.assertEqual(insight.tone, "warn")
        self.assertIn("due", insight.headline)

    def test_thin_evidence_is_labelled_as_a_hint(self):
        for days in (45, 30, 15):
            self._fill(days)

        insight = fuel_rhythm()
        self.assertEqual(insight.confidence, "thin")
        self.assertIn("treat as a hint", insight.confidence_note)

    def test_plenty_of_evidence_reads_as_solid(self):
        for days in range(200, 0, -14):
            self._fill(days)

        self.assertEqual(fuel_rhythm().confidence, "solid")


class CardPromoCoverageTests(TestCase):
    def test_nothing_without_purchases_at_mapped_places(self):
        Promo.objects.create(
            title="Deal", issuer="BPI", category=SpendCategory.GROCERY,
            discount_pct=Decimal("10"),
            ends_on=timezone.localdate() + timedelta(days=20),
        )
        self.assertIsNone(card_promo_coverage())

    def test_a_promo_where_you_shop_is_surfaced(self):
        place = make_place(osm_id=7, kind=PlaceKind.SUPERMARKET,
                           name="Puregold", brand="Puregold")
        Promo.objects.create(
            title="10% off", issuer="BPI", brand="Puregold",
            category=SpendCategory.GROCERY, discount_pct=Decimal("10"),
            ends_on=timezone.localdate() + timedelta(days=20),
        )
        Purchase.objects.create(place=place, category=SpendCategory.GROCERY,
                                total=Decimal("1000"))

        insight = card_promo_coverage()
        self.assertIn("card promo", insight.headline)
        # Names the qualifying card without knowing whether you hold it.
        self.assertIn("Any BPI card", insight.detail)


class OtherInsightTests(TestCase):
    def test_a_promo_ending_soon_is_surfaced(self):
        Promo.objects.create(
            title="Half price coffee", category=SpendCategory.DINING,
            discount_pct=Decimal("50"),
            ends_on=timezone.localdate() + timedelta(days=1),
        )
        insight = promo_watch()
        self.assertIn("ending within 3 days", insight.headline)

    def test_a_promo_far_off_is_not_nagged_about(self):
        Promo.objects.create(
            title="Season sale", category=SpendCategory.APPAREL,
            discount_pct=Decimal("20"),
            ends_on=timezone.localdate() + timedelta(days=40),
        )
        self.assertIsNone(promo_watch())

    def test_cheap_unworn_clothing_is_not_worth_mentioning(self):
        purchase = Purchase.objects.create(
            category=SpendCategory.APPAREL, merchant="Uniqlo", total=Decimal("300")
        )
        PurchaseItem.objects.create(purchase=purchase, description="Socks",
                                    amount=Decimal("300"), is_wearable=True)
        self.assertIsNone(idle_wardrobe())

    def test_expensive_unworn_clothing_is(self):
        purchase = Purchase.objects.create(
            category=SpendCategory.APPAREL, merchant="Uniqlo", total=Decimal("4000")
        )
        PurchaseItem.objects.create(purchase=purchase, description="Coat",
                                    amount=Decimal("4000"), is_wearable=True)

        insight = idle_wardrobe()
        self.assertIn("never worn", insight.headline)

    def test_a_falling_commodity_is_reported_as_good_news(self):
        commodity = Commodity.objects.create(
            category=CommodityCategory.POULTRY, name="Chicken Thigh"
        )
        for day, price in ((3, "200"), (7, "180"), (11, "150")):
            CommodityPrice.objects.create(
                commodity=commodity, observed_on=date(2026, 8, day),
                price=Decimal(price),
            )

        insight = grocery_swing()
        self.assertIsNotNone(insight)
        self.assertEqual(insight.tone, "good")


class BriefingTests(TestCase):
    def test_an_empty_database_says_nothing_and_explains_why(self):
        briefing = build_briefing()

        self.assertFalse(briefing.has_anything)
        # Silence should be explained, not just look like an empty screen.
        self.assertEqual(len(briefing.learning), len(REQUIREMENTS))

    def test_producing_an_insight_removes_it_from_the_waiting_list(self):
        Promo.objects.create(
            title="Deal", category=SpendCategory.DINING, discount_pct=Decimal("50"),
            ends_on=timezone.localdate() + timedelta(days=1),
        )
        briefing = build_briefing()

        self.assertTrue(briefing.has_anything)
        self.assertNotIn(REQUIREMENTS["promo_watch"], briefing.learning)

    def test_the_most_urgent_insight_leads(self):
        # A warning outranks anything merely informational.
        Promo.objects.create(
            title="Deal", category=SpendCategory.DINING, discount_pct=Decimal("50"),
            ends_on=timezone.localdate() + timedelta(days=1),
        )
        place = make_place(osm_id=8, kind=PlaceKind.SUPERMARKET, brand="Puregold")
        Promo.objects.create(
            title="Bank deal", issuer="BPI", brand="Puregold",
            category=SpendCategory.GROCERY, discount_pct=Decimal("10"),
            ends_on=timezone.localdate() + timedelta(days=40),
        )
        Purchase.objects.create(place=place, category=SpendCategory.GROCERY,
                                total=Decimal("500"))

        briefing = build_briefing()
        self.assertEqual(briefing.insights[0].tone, "warn")


class BriefingScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_it_renders_empty(self):
        response = self.client.get(reverse("insights:briefing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing to report yet")

    def test_it_renders_with_an_insight(self):
        Promo.objects.create(
            title="Half price", category=SpendCategory.DINING,
            discount_pct=Decimal("50"),
            ends_on=timezone.localdate() + timedelta(days=1),
        )
        response = self.client.get(reverse("insights:briefing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ending within 3 days")

    def test_signed_out_users_reach_nothing(self):
        self.client.logout()
        response = self.client.get(reverse("insights:briefing"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
