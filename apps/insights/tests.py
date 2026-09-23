"""Tests for the briefing."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.categories import SpendCategory
from apps.grocery.models import Commodity, CommodityCategory, CommodityPrice
from apps.places.models import Place, PlaceKind
from apps.spend.models import Promo, Purchase

from .services import REQUIREMENTS, build_briefing, card_promo_coverage, grocery_swing


def make_place(osm_id=1, **overrides) -> Place:
    values = {
        "kind": PlaceKind.SUPERMARKET, "osm_type": Place.OSMType.NODE,
        "osm_id": osm_id, "name": "Puregold", "brand": "Puregold",
        "latitude": Decimal("14.58"), "longitude": Decimal("121.06"),
        "region": "NCR",
    }
    values.update(overrides)
    return Place.objects.create(**values)


class CardPromoCoverageTests(TestCase):
    def test_nothing_without_purchases_at_mapped_places(self):
        Promo.objects.create(
            title="Deal", issuer="BPI", category=SpendCategory.GROCERY,
            discount_pct=Decimal("10"),
            ends_on=timezone.localdate() + timedelta(days=20),
        )
        self.assertIsNone(card_promo_coverage())

    def test_a_promo_where_you_shop_is_surfaced(self):
        place = make_place(osm_id=7)
        Promo.objects.create(
            title="10% off", issuer="BPI", brand="Puregold",
            category=SpendCategory.GROCERY, discount_pct=Decimal("10"),
            ends_on=timezone.localdate() + timedelta(days=20),
        )
        Purchase.objects.create(place=place, category=SpendCategory.GROCERY,
                                total=Decimal("1000"))

        insight = card_promo_coverage()
        self.assertIn("card promo", insight.headline)
        self.assertIn("Any BPI card", insight.detail)


class GroceryInsightTests(TestCase):
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
        self.assertEqual(len(briefing.learning), len(REQUIREMENTS))

    def test_producing_an_insight_removes_it_from_the_waiting_list(self):
        place = make_place(osm_id=8)
        Promo.objects.create(
            title="Bank deal", issuer="BPI", brand="Puregold",
            category=SpendCategory.GROCERY, discount_pct=Decimal("10"),
            ends_on=timezone.localdate() + timedelta(days=40),
        )
        Purchase.objects.create(place=place, category=SpendCategory.GROCERY,
                                total=Decimal("500"))

        briefing = build_briefing()

        self.assertTrue(briefing.has_anything)
        self.assertNotIn(REQUIREMENTS["card_promo_coverage"], briefing.learning)


class BriefingScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_it_renders_empty(self):
        response = self.client.get(reverse("insights:briefing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing to report yet")

    def test_it_renders_with_an_insight(self):
        place = make_place(osm_id=9)
        Promo.objects.create(
            title="Bank deal", issuer="BPI", brand="Puregold",
            category=SpendCategory.GROCERY, discount_pct=Decimal("10"),
            ends_on=timezone.localdate() + timedelta(days=40),
        )
        Purchase.objects.create(place=place, category=SpendCategory.GROCERY,
                                total=Decimal("500"))

        response = self.client.get(reverse("insights:briefing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "card promo")

    def test_signed_out_users_read_the_briefing(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("insights:briefing")).status_code, 200
        )
