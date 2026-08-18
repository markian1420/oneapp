"""
Tests for "where to buy".

The property that matters most is restraint: the screen must not imply it knows
what a shop charges. It ranks by distance, reports a price only where one was
logged, and says so plainly everywhere else.
"""

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

from .models import Promo, Purchase, PurchaseItem
from .shopping import known_prices, reference_price, where_to_buy

# Ortigas Center, which is where the examples in this file are standing.
ORIGIN = (14.5866, 121.0614)


def make_place(osm_id, name, brand="", kind=PlaceKind.SUPERMARKET,
               lat="14.5900", lng="121.0620") -> Place:
    return Place.objects.create(
        kind=kind, osm_type=Place.OSMType.NODE, osm_id=osm_id,
        name=name, brand=brand,
        latitude=Decimal(lat), longitude=Decimal(lng),
        city="Pasig", region="NCR",
    )


class NearbyTests(TestCase):
    def setUp(self):
        self.near = make_place(1, "Puregold Pasig", "Puregold",
                               lat="14.5870", lng="121.0616")
        self.far = make_place(2, "Landers Balintawak", "Landers",
                              lat="14.6500", lng="121.0000")
        self.wrong_kind = make_place(3, "Shell", "Shell", kind=PlaceKind.FUEL)

    def test_only_places_that_sell_the_category_are_returned(self):
        options = where_to_buy(origin=ORIGIN, category=SpendCategory.GROCERY)
        names = [o.place.name for o in options]

        self.assertIn("Puregold Pasig", names)
        self.assertNotIn("Shell", names)

    def test_the_nearest_leads_when_nothing_else_is_known(self):
        options = where_to_buy(origin=ORIGIN, category=SpendCategory.GROCERY)
        self.assertEqual(options[0].place.name, "Puregold Pasig")

    def test_somewhere_beyond_the_radius_is_left_out(self):
        make_place(4, "Cebu store", lat="10.3000", lng="123.9000")
        names = [
            o.place.name
            for o in where_to_buy(origin=ORIGIN, category=SpendCategory.GROCERY)
        ]
        self.assertNotIn("Cebu store", names)

    def test_a_category_with_no_place_kinds_returns_nothing(self):
        self.assertEqual(
            where_to_buy(origin=ORIGIN, category=SpendCategory.UTILITIES), []
        )

    def test_malls_answer_the_apparel_question(self):
        make_place(5, "SM East Ortigas", "SM", kind=PlaceKind.MALL,
                   lat="14.5880", lng="121.0700")
        options = where_to_buy(origin=ORIGIN, category=SpendCategory.APPAREL)
        self.assertEqual([o.place.name for o in options], ["SM East Ortigas"])


class KnownPriceTests(TestCase):
    def setUp(self):
        self.puregold = make_place(1, "Puregold", "Puregold",
                                   lat="14.5870", lng="121.0616")
        self.landers = make_place(2, "Landers", "Landers",
                                  lat="14.5890", lng="121.0640")

    def _log(self, place, description, unit_price):
        purchase = Purchase.objects.create(
            place=place, category=SpendCategory.GROCERY, total=Decimal("1000")
        )
        PurchaseItem.objects.create(
            purchase=purchase, description=description,
            quantity=Decimal("1"), unit_price=Decimal(unit_price),
            amount=Decimal(unit_price),
        )

    def test_nothing_is_known_before_anything_is_logged(self):
        self.assertEqual(known_prices("chicken"), {})

    def test_a_logged_line_gives_that_shop_a_price(self):
        self._log(self.puregold, "Chicken breast", "215.00")

        prices = known_prices("chicken")
        self.assertEqual(prices[self.puregold.pk], (Decimal("215.00"), 1))

    def test_repeat_visits_average_rather_than_take_the_latest(self):
        # A single receipt is as likely to catch a promotion as a normal price.
        self._log(self.puregold, "Chicken breast", "200.00")
        self._log(self.puregold, "Chicken breast", "240.00")

        self.assertEqual(known_prices("chicken")[self.puregold.pk],
                         (Decimal("220.00"), 2))

    def test_shops_with_a_price_lead_the_list_over_merely_closer_ones(self):
        self._log(self.landers, "Chicken breast", "198.00")

        options = where_to_buy(origin=ORIGIN, category=SpendCategory.GROCERY,
                               item="chicken")
        # Landers is further, but it is the only row carrying information.
        self.assertEqual(options[0].place.name, "Landers")
        self.assertTrue(options[0].has_price)
        self.assertFalse(options[1].has_price)

    def test_a_shop_with_no_price_says_so_rather_than_showing_zero(self):
        options = where_to_buy(origin=ORIGIN, category=SpendCategory.GROCERY,
                               item="chicken")
        self.assertIsNone(options[0].your_price)
        self.assertIn("Nothing known", options[0].why)


class PromoTests(TestCase):
    def setUp(self):
        self.uniqlo = make_place(1, "Uniqlo SM East Ortigas", "Uniqlo",
                                 kind=PlaceKind.MALL, lat="14.5880", lng="121.0700")
        self.other = make_place(2, "Ayala Malls", "Ayala",
                                kind=PlaceKind.MALL, lat="14.5890", lng="121.0650")

    def test_a_brand_promo_attaches_only_to_that_brand(self):
        Promo.objects.create(
            title="20% OFF at Uniqlo", brand="Uniqlo", issuer="Metrobank",
            category=SpendCategory.APPAREL, discount_pct=Decimal("20"),
            ends_on=timezone.localdate() + timedelta(days=10),
        )
        options = {o.place.brand: o for o in
                   where_to_buy(origin=ORIGIN, category=SpendCategory.APPAREL)}

        self.assertEqual(len(options["Uniqlo"].promos), 1)
        self.assertEqual(options["Ayala"].promos, [])

    def test_a_category_wide_promo_is_kept_out_of_the_per_place_column(self):
        Promo.objects.create(
            title="5% on all clothing", issuer="BPI",
            category=SpendCategory.APPAREL, discount_pct=Decimal("5"),
            ends_on=timezone.localdate() + timedelta(days=10),
        )
        options = where_to_buy(origin=ORIGIN, category=SpendCategory.APPAREL)

        # It applies at every mall, so putting it in every row made them all
        # look identical and told you nothing about which to pick. It belongs
        # beside the list, once.
        self.assertTrue(all(not o.promos for o in options))
        self.assertTrue(all(len(o.general_promos) == 1 for o in options))

    def test_a_brand_promo_still_outranks_a_merely_closer_place(self):
        Promo.objects.create(
            title="20% OFF at Uniqlo", brand="Uniqlo", issuer="Metrobank",
            category=SpendCategory.APPAREL, discount_pct=Decimal("20"),
            ends_on=timezone.localdate() + timedelta(days=10),
        )
        Promo.objects.create(
            title="5% on all clothing", issuer="BPI",
            category=SpendCategory.APPAREL, discount_pct=Decimal("5"),
            ends_on=timezone.localdate() + timedelta(days=10),
        )
        options = where_to_buy(origin=ORIGIN, category=SpendCategory.APPAREL)
        self.assertEqual(options[0].place.brand, "Uniqlo")

    def test_an_expired_promo_is_not_attached(self):
        Promo.objects.create(
            title="Old sale", brand="Uniqlo", issuer="Metrobank",
            category=SpendCategory.APPAREL, discount_pct=Decimal("50"),
            ends_on=timezone.localdate() - timedelta(days=1),
        )
        options = where_to_buy(origin=ORIGIN, category=SpendCategory.APPAREL)
        self.assertTrue(all(not o.promos for o in options))

    def test_a_place_with_a_promo_leads_over_one_without(self):
        Promo.objects.create(
            title="20% OFF at Uniqlo", brand="Uniqlo", issuer="Metrobank",
            category=SpendCategory.APPAREL, discount_pct=Decimal("20"),
            ends_on=timezone.localdate() + timedelta(days=10),
        )
        options = where_to_buy(origin=ORIGIN, category=SpendCategory.APPAREL)
        self.assertEqual(options[0].place.brand, "Uniqlo")


class ReferencePriceTests(TestCase):
    def setUp(self):
        self.commodity = Commodity.objects.create(
            category=CommodityCategory.POULTRY,
            name="Chicken Breast, Local", unit="kg",
        )
        CommodityPrice.objects.create(
            commodity=self.commodity, observed_on=date(2026, 8, 17),
            price=Decimal("215.80"),
        )

    def test_the_da_price_is_offered_as_a_benchmark(self):
        reference = reference_price("chicken")
        self.assertEqual(reference["price"], Decimal("215.80"))
        self.assertEqual(reference["unit"], "kg")

    def test_an_item_the_da_does_not_track_has_no_benchmark(self):
        self.assertIsNone(reference_price("running shoes"))

    def test_a_blank_item_has_no_benchmark(self):
        self.assertIsNone(reference_price(""))


class WhereScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("shopper", password="not-a-real-password")
        self.client.force_login(self.user)
        make_place(1, "Puregold Pasig", "Puregold")

    def test_without_a_location_it_asks_for_one(self):
        response = self.client.get(reverse("spend:where"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Where are you?")
        self.assertEqual(response.context["options"], [])

    def test_with_a_location_it_lists_nearby_places(self):
        response = self.client.get(reverse("spend:where"), {
            "lat": ORIGIN[0], "lng": ORIGIN[1], "category": SpendCategory.GROCERY,
        })
        self.assertEqual(len(response.context["options"]), 1)

    def test_it_states_why_it_is_not_ranking_by_price(self):
        response = self.client.get(reverse("spend:where"), {
            "lat": ORIGIN[0], "lng": ORIGIN[1],
        })
        # The screen must not imply it knows what a shop charges.
        self.assertContains(response, "Sorted by distance, not by price")
        self.assertContains(response, "No Philippine supermarket publishes shelf prices")

    def test_a_nonsense_location_falls_back_to_asking(self):
        response = self.client.get(reverse("spend:where"),
                                   {"lat": "abc", "lng": "def"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["origin"])

    def test_signed_out_users_reach_nothing(self):
        self.client.logout()
        response = self.client.get(reverse("spend:where"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])


class ShopKindPriorityTests(TestCase):
    """Metro Manila has 3,670 convenience stores against 733 supermarkets."""

    def setUp(self):
        # A 7-Eleven right on top of you, a Puregold a little further.
        make_place(1, "7-Eleven", "7-Eleven", kind=PlaceKind.CONVENIENCE,
                   lat="14.5867", lng="121.0615")
        make_place(2, "Puregold Pasig", "Puregold", kind=PlaceKind.SUPERMARKET,
                   lat="14.5900", lng="121.0640")

    def test_a_supermarket_leads_a_nearer_convenience_store(self):
        options = where_to_buy(origin=ORIGIN, category=SpendCategory.GROCERY)

        # By distance alone the answer to "where do I buy chicken" is nine
        # 7-Elevens: technically nearest, useless as advice.
        self.assertEqual(options[0].place.brand, "Puregold")
        self.assertTrue(options[1].is_convenience)

    def test_convenience_stores_are_still_offered_rather_than_hidden(self):
        options = where_to_buy(origin=ORIGIN, category=SpendCategory.GROCERY)
        self.assertEqual(len(options), 2)

    def test_a_logged_price_beats_the_kind_ranking(self):
        # Somewhere you have actually bought the thing outranks a general
        # preference for supermarkets.
        store = Place.objects.get(osm_id=1)
        purchase = Purchase.objects.create(
            place=store, category=SpendCategory.GROCERY, total=Decimal("100")
        )
        PurchaseItem.objects.create(
            purchase=purchase, description="Chicken", quantity=Decimal("1"),
            unit_price=Decimal("199.00"), amount=Decimal("199.00"),
        )

        options = where_to_buy(origin=ORIGIN, category=SpendCategory.GROCERY,
                               item="chicken")
        self.assertEqual(options[0].place.brand, "7-Eleven")
        self.assertTrue(options[0].has_price)
