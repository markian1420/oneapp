"""Tests for purchases, promos and wardrobe economics."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cards.models import Card, Reward
from apps.core.categories import SpendCategory
from apps.fuel.models import FillUp, Vehicle
from apps.places.models import Place, PlaceKind

from .models import Promo, Purchase, PurchaseItem
from .services import (
    expiring_promos,
    live_promos,
    spend_by_category,
    stale_promos,
    wardrobe,
    wardrobe_summary,
)


def make_purchase(**overrides) -> Purchase:
    values = {
        "category": SpendCategory.GROCERY,
        "merchant": "Puregold",
        "total": Decimal("1000.00"),
    }
    values.update(overrides)
    return Purchase.objects.create(**values)


class PurchaseTests(TestCase):
    def test_where_falls_back_from_place_to_merchant(self):
        place = Place.objects.create(
            kind=PlaceKind.SUPERMARKET, osm_type=Place.OSMType.NODE, osm_id=1,
            name="Puregold Pasig", brand="Puregold",
            latitude=Decimal("14.58"), longitude=Decimal("121.06"),
        )
        # display_name does not repeat a brand already inside the name.
        self.assertEqual(make_purchase(place=place).where, "Puregold Pasig")
        self.assertEqual(make_purchase(merchant="Corner store").where, "Corner store")

    def test_lines_that_do_not_add_up_are_flagged_not_corrected(self):
        purchase = make_purchase(total=Decimal("1000"))
        PurchaseItem.objects.create(
            purchase=purchase, description="Rice", amount=Decimal("400")
        )

        # Silently rewriting the total would hide a missing line.
        self.assertTrue(purchase.items_disagree)
        self.assertEqual(purchase.total, Decimal("1000.00"))

    def test_small_rounding_between_lines_and_total_is_tolerated(self):
        purchase = make_purchase(total=Decimal("1000"))
        PurchaseItem.objects.create(
            purchase=purchase, description="Rice", amount=Decimal("999.50")
        )
        self.assertFalse(purchase.items_disagree)

    def test_a_purchase_with_no_lines_never_disagrees(self):
        self.assertFalse(make_purchase().items_disagree)


class CostPerWearTests(TestCase):
    def setUp(self):
        self.purchase = make_purchase(category=SpendCategory.APPAREL)

    def _item(self, amount, wears=0, wearable=True):
        return PurchaseItem.objects.create(
            purchase=self.purchase, description="Jacket",
            amount=Decimal(amount), wears=wears, is_wearable=wearable,
        )

    def test_cost_per_wear_is_price_over_wears(self):
        self.assertEqual(self._item("3000", 100).cost_per_wear, Decimal("30.00"))

    def test_an_unworn_item_has_no_cost_per_wear(self):
        # Not zero: zero would read as free, which is the opposite of true.
        self.assertIsNone(self._item("3000", 0).cost_per_wear)

    def test_an_untracked_item_is_left_out(self):
        self.assertIsNone(self._item("3000", 10, wearable=False).cost_per_wear)

    def test_the_verdict_reflects_the_number(self):
        self.assertEqual(self._item("3000", 100).verdict, "Earned its price")
        self.assertEqual(self._item("600", 2).verdict, "Expensive per wear so far")
        self.assertEqual(self._item("500", 0).verdict, "Not worn yet")

    def test_unworn_items_lead_the_wardrobe(self):
        self._item("600", 5)
        unworn = self._item("4000", 0)

        # The unworn expensive thing is what the screen exists to make you see.
        self.assertEqual(wardrobe()[0].pk, unworn.pk)

    def test_the_summary_averages_across_everything(self):
        self._item("1000", 10)
        self._item("1000", 10)

        summary = wardrobe_summary()
        self.assertEqual(summary["items"], 2)
        self.assertEqual(summary["wears"], 20)
        self.assertEqual(summary["cost_per_wear"], Decimal("100.00"))


class PromoTests(TestCase):
    def _promo(self, **overrides) -> Promo:
        values = {
            "title": "Deal", "category": SpendCategory.DINING,
            "discount_pct": Decimal("20"),
        }
        values.update(overrides)
        return Promo.objects.create(**values)

    def test_a_promo_outside_its_dates_is_not_live(self):
        today = timezone.localdate()
        self.assertFalse(self._promo(ends_on=today - timedelta(days=1)).is_live)
        self.assertFalse(self._promo(starts_on=today + timedelta(days=1)).is_live)
        self.assertTrue(self._promo(ends_on=today + timedelta(days=5)).is_live)

    def test_status_distinguishes_ending_from_merely_live(self):
        today = timezone.localdate()
        self.assertEqual(self._promo(ends_on=today + timedelta(days=2)).status, "ending")
        self.assertEqual(self._promo(ends_on=today + timedelta(days=30)).status, "live")
        self.assertEqual(self._promo(ends_on=today - timedelta(days=1)).status, "expired")

    def test_undated_promos_sort_last(self):
        today = timezone.localdate()
        self._promo(title="Undated")
        self._promo(title="Dated", ends_on=today + timedelta(days=10))

        # An undated promo is the one most likely to have quietly ended, so it
        # should not lead the list.
        self.assertEqual([p.title for p in live_promos()], ["Dated", "Undated"])

    def test_expiring_only_catches_the_ones_about_to_go(self):
        today = timezone.localdate()
        self._promo(title="Soon", ends_on=today + timedelta(days=1))
        self._promo(title="Later", ends_on=today + timedelta(days=20))

        self.assertEqual([p.title for p in expiring_promos()], ["Soon"])

    def test_old_undated_promos_are_flagged_for_review(self):
        promo = self._promo(title="Ancient")
        Promo.objects.filter(pk=promo.pk).update(
            added_at=timezone.now() - timedelta(days=90)
        )
        self.assertEqual([p.title for p in stale_promos()], ["Ancient"])

    def test_filtering_by_category_and_brand(self):
        self._promo(title="Food", category=SpendCategory.DINING, brand="Jollibee")
        self._promo(title="Clothes", category=SpendCategory.APPAREL)

        self.assertEqual(
            [p.title for p in live_promos(category=SpendCategory.APPAREL)], ["Clothes"]
        )
        self.assertEqual([p.title for p in live_promos(brand="jollibee")], ["Food"])


class SpendSummaryTests(TestCase):
    def test_fuel_is_counted_even_though_it_lives_elsewhere(self):
        vehicle = Vehicle.objects.create(name="Car", is_default=True)
        place = Place.objects.create(
            kind=PlaceKind.FUEL, osm_type=Place.OSMType.NODE, osm_id=1,
            name="Shell", brand="Shell",
            latitude=Decimal("14.58"), longitude=Decimal("121.06"),
        )
        FillUp.objects.create(
            vehicle=vehicle, place=place, fuel_type="gas_95",
            liters=Decimal("40"), price_per_liter=Decimal("78.5"),
            total_cost=Decimal("3140"),
        )
        make_purchase(total=Decimal("1000"))

        totals = {row.category: row for row in spend_by_category()}
        # A summary that silently omits the biggest recurring cost is worse
        # than no summary.
        self.assertEqual(totals["fuel"].spent, Decimal("3140"))
        self.assertEqual(totals["grocery"].spent, Decimal("1000"))

    def test_the_biggest_category_leads(self):
        make_purchase(category=SpendCategory.GROCERY, total=Decimal("5000"))
        make_purchase(category=SpendCategory.DINING, total=Decimal("500"))

        self.assertEqual(spend_by_category()[0].category, SpendCategory.GROCERY)

    def test_a_category_with_no_spend_still_appears_at_zero(self):
        categories = {row.category for row in spend_by_category()}
        self.assertEqual(categories, set(SpendCategory.values))


class SpendScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("spender", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_screens_render_empty(self):
        for name in ("spend:purchases", "spend:promos", "spend:wardrobe",
                     "spend:purchase_create"):
            with self.subTest(screen=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_screens_render_with_data(self):
        purchase = make_purchase(category=SpendCategory.APPAREL)
        PurchaseItem.objects.create(
            purchase=purchase, description="Shirt", amount=Decimal("600"),
            is_wearable=True, wears=3,
        )
        Promo.objects.create(
            title="Sale", category=SpendCategory.APPAREL,
            discount_pct=Decimal("30"),
            ends_on=timezone.localdate() + timedelta(days=5),
        )

        for url in (reverse("spend:purchases"), reverse("spend:promos"),
                    reverse("spend:wardrobe"),
                    reverse("spend:purchase_detail", args=[purchase.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_recording_a_wear_increments_it(self):
        purchase = make_purchase(category=SpendCategory.APPAREL)
        item = PurchaseItem.objects.create(
            purchase=purchase, description="Shirt", amount=Decimal("600"),
            is_wearable=True,
        )
        self.client.post(reverse("spend:item_wear", args=[item.pk]))

        item.refresh_from_db()
        self.assertEqual(item.wears, 1)

    def test_the_purchase_screen_checks_the_card_used(self):
        best = Card.objects.create(name="Grocery card")
        Reward.objects.create(card=best, rate=Decimal("5"),
                              category=SpendCategory.GROCERY)
        worse = Card.objects.create(name="Base card")
        Reward.objects.create(card=worse, rate=Decimal("1"))

        purchase = make_purchase(card=worse, total=Decimal("1000"))
        response = self.client.get(
            reverse("spend:purchase_detail", args=[purchase.pk])
        )

        self.assertEqual(response.context["best_pick"].card, best)
        self.assertEqual(response.context["used_pick"].card, worse)

    def test_a_purchase_needs_a_place_or_a_name(self):
        response = self.client.post(reverse("spend:purchase_create"), {
            "category": SpendCategory.GROCERY, "total": "500",
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Purchase.objects.exists())

    def test_signed_out_users_reach_nothing(self):
        self.client.logout()
        response = self.client.get(reverse("spend:purchases"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
