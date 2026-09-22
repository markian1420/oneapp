"""Tests for purchases and public card promos."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.categories import SpendCategory
from apps.core.tables import DEFAULT_PAGE_SIZE
from apps.places.models import Place, PlaceKind

from .models import Promo, Purchase, PurchaseItem
from .services import expiring_promos, live_promos, stale_promos


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


class PurchaseItemEconomicsTests(TestCase):
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


class SpendScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("spender", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_screens_render_empty(self):
        for name in ("spend:where", "spend:card_promos"):
            with self.subTest(screen=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_screens_render_with_data(self):
        Promo.objects.create(
            title="Sale", issuer="BPI", category=SpendCategory.APPAREL,
            discount_pct=Decimal("30"),
            ends_on=timezone.localdate() + timedelta(days=5),
        )

        for url in (reverse("spend:where"), reverse("spend:card_promos")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_card_promos_search_and_page_on_the_server(self):
        """The browser gets one page of rows, never the whole set."""
        for n in range(15):
            Promo.objects.create(
                title=f"Coffee deal {n}", issuer="RCBC", brand="Il Padrino",
                category=SpendCategory.DINING, discount_pct=Decimal("30"),
                ends_on=timezone.localdate() + timedelta(days=30),
            )
        Promo.objects.create(
            title="Shoe sale", issuer="BPI", category=SpendCategory.APPAREL,
            discount_pct=Decimal("20"),
            ends_on=timezone.localdate() + timedelta(days=30),
        )

        page = self.client.get(reverse("spend:card_promos"))
        self.assertEqual(page.context["total"], 16)
        self.assertEqual(len(page.context["rows"]), DEFAULT_PAGE_SIZE)

        found = self.client.get(reverse("spend:card_promos"), {"q": "shoe"})
        self.assertEqual([r["title"] for r in found.context["rows"]], ["Shoe sale"])

        by_bank = self.client.get(reverse("spend:card_promos"), {"q": "rcbc"})
        self.assertEqual(by_bank.context["total"], 15)

    def test_a_search_survives_sorting_and_paging(self):
        """Sorting a filtered table must not quietly drop the filter."""
        Promo.objects.create(
            title="Shoe sale", issuer="BPI", category=SpendCategory.APPAREL,
            discount_pct=Decimal("20"),
            ends_on=timezone.localdate() + timedelta(days=30),
        )
        response = self.client.get(
            reverse("spend:card_promos"), {"q": "shoe", "sort": "title"}
        )

        self.assertEqual(response.context["total"], 1)
        sortable = [h for h in response.context["table"].headers if h.sortable]
        self.assertTrue(all("q=shoe" in h.url for h in sortable))

    def test_signed_out_users_reach_nothing(self):
        self.client.logout()
        for name in ("spend:where", "spend:card_promos"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login/", response["Location"])


class CardPromoTests(TestCase):
    """A directory of bank offers that holds nobody's card."""

    def setUp(self):
        self.user = User.objects.create_user("shopper", password="not-a-real-password")
        self.client.force_login(self.user)

    def _promo(self, **overrides):
        values = {
            "title": "Dining deal", "category": SpendCategory.DINING,
            "discount_pct": Decimal("15"),
            "ends_on": timezone.localdate() + timedelta(days=30),
        }
        values.update(overrides)
        return Promo.objects.create(**values)

    def test_an_issuer_is_what_makes_it_a_card_promo(self):
        self.assertFalse(self._promo(title="Store sale").is_card_promo)
        self.assertTrue(self._promo(title="BPI deal", issuer="BPI").is_card_promo)

    def test_qualifies_reads_as_a_sentence(self):
        self.assertEqual(self._promo().qualifies, "Any payment")
        self.assertEqual(self._promo(issuer="BDO").qualifies, "Any BDO card")
        self.assertEqual(
            self._promo(issuer="BPI", card_name="Gold Rewards").qualifies,
            "BPI Gold Rewards",
        )

    def test_the_screen_shows_card_promos_only(self):
        self._promo(title="Anyone can use this")
        self._promo(title="Needs a BPI card", issuer="BPI")

        response = self.client.get(reverse("spend:card_promos"))
        self.assertEqual(
            [r["promo"].title for r in response.context["rows"]],
            ["Needs a BPI card"],
        )

    def test_the_list_is_paged_rather_than_rendered_whole(self):
        # One bank alone publishes hundreds of live promos; rendering them all
        # is the exact thing server-side paging exists to avoid.
        for index in range(25):
            self._promo(title=f"Deal {index}", issuer="BPI")

        response = self.client.get(reverse("spend:card_promos"))
        self.assertEqual(response.context["total"], 25)
        self.assertLess(len(response.context["rows"]), 25)

    def test_filtering_by_bank(self):
        self._promo(title="A", issuer="BPI")
        self._promo(title="C", issuer="BDO")

        response = self.client.get(reverse("spend:card_promos"), {"issuer": "BDO"})
        self.assertEqual([r["promo"].title for r in response.context["rows"]], ["C"])

    def test_an_expired_card_promo_is_not_listed(self):
        self._promo(title="Old", issuer="BPI",
                    ends_on=timezone.localdate() - timedelta(days=1))
        self.assertEqual(
            list(self.client.get(reverse("spend:card_promos")).context["rows"]), []
        )

    def test_a_brand_promo_and_a_category_promo_both_match_a_place(self):
        from .services import card_promos_at

        place = Place.objects.create(
            kind=PlaceKind.FAST_FOOD, osm_type=Place.OSMType.NODE, osm_id=5,
            name="Jollibee", brand="Jollibee",
            latitude=Decimal("14.58"), longitude=Decimal("121.06"),
        )
        self._promo(title="At Jollibee", issuer="BPI", brand="Jollibee")
        self._promo(title="All dining", issuer="BDO", category=SpendCategory.DINING)
        self._promo(title="Groceries", issuer="BDO", category=SpendCategory.GROCERY)

        titles = {p.title for p in card_promos_at(place)}
        self.assertEqual(titles, {"At Jollibee", "All dining"})

    def test_the_screen_says_plainly_that_no_card_is_stored(self):
        response = self.client.get(reverse("spend:card_promos"))
        self.assertContains(response, "No card of yours is stored")

    def test_there_is_nowhere_in_the_app_to_store_a_card(self):
        # The guarantee, pinned: no card model exists to write to.
        from django.apps import apps

        names = {m.__name__.lower() for m in apps.get_models()}
        self.assertNotIn("card", names)
        self.assertNotIn("reward", names)
