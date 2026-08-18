"""
Tests for card ranking.

Weighted towards the arithmetic that decides which card the app tells you to
tap, because that is the advice a person acts on at a counter without checking.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.categories import SpendCategory, category_for_place
from apps.places.models import Place, PlaceKind

from .models import Card, Reward
from .services import coverage_gaps, expiring_rewards, rank_cards, wallet_summary


def make_card(name="Card", **overrides) -> Card:
    return Card.objects.create(name=name, **overrides)


def add_reward(card, rate, **overrides) -> Reward:
    values = {"card": card, "rate": Decimal(str(rate))}
    values.update(overrides)
    return Reward.objects.create(**values)


class RewardValueTests(TestCase):
    def setUp(self):
        self.card = make_card()

    def test_cashback_is_a_straight_percentage(self):
        reward = add_reward(self.card, "5")
        self.assertEqual(reward.value_on(Decimal("1000")), Decimal("50.00"))

    def test_points_are_converted_so_cards_can_be_compared(self):
        # 3 points per 100 pesos, each worth 0.50, is really 1.5% back.
        # Comparing "3 points" against "2% cashback" without converting is how
        # people pick the worse card.
        reward = add_reward(
            self.card, "3", kind=Reward.Kind.POINTS,
            peso_per_point=Decimal("0.5"),
        )
        self.assertEqual(reward.value_on(Decimal("1000")), Decimal("15.00"))

    def test_a_monthly_cap_limits_the_return(self):
        reward = add_reward(self.card, "5", monthly_cap=Decimal("200"))
        self.assertEqual(reward.value_on(Decimal("10000")), Decimal("200.00"))

    def test_below_the_minimum_spend_it_earns_nothing(self):
        reward = add_reward(self.card, "5", min_spend=Decimal("500"))
        self.assertEqual(reward.value_on(Decimal("100")), Decimal("0.00"))
        self.assertEqual(reward.value_on(Decimal("500")), Decimal("25.00"))

    def test_a_rule_outside_its_dates_is_not_current(self):
        today = timezone.localdate()
        past = add_reward(self.card, "5", valid_to=today - timedelta(days=1))
        future = add_reward(self.card, "5", valid_from=today + timedelta(days=1))
        live = add_reward(self.card, "5", valid_to=today + timedelta(days=30))

        self.assertFalse(past.is_current)
        self.assertFalse(future.is_current)
        self.assertTrue(live.is_current)

    def test_a_rate_ending_within_a_fortnight_is_flagged(self):
        today = timezone.localdate()
        soon = add_reward(self.card, "5", valid_to=today + timedelta(days=7))
        later = add_reward(self.card, "5", valid_to=today + timedelta(days=60))

        self.assertTrue(soon.expires_soon)
        self.assertFalse(later.expires_soon)


class RankingTests(TestCase):
    def setUp(self):
        self.everyday = make_card("Everyday")
        add_reward(self.everyday, "1")                      # base rate, anything

        self.fuel_card = make_card("Fuel card")
        add_reward(self.fuel_card, "5", category=SpendCategory.FUEL)

        self.shell_card = make_card("Shell card")
        add_reward(self.shell_card, "3", brand="Shell")

    def test_the_category_specialist_wins_in_its_category(self):
        picks = rank_cards(category=SpendCategory.FUEL, amount=Decimal("1000"))
        self.assertEqual(picks[0].card.name, "Fuel card")
        self.assertEqual(picks[0].value, Decimal("50.00"))

    def test_the_base_rate_wins_where_nothing_else_applies(self):
        picks = rank_cards(category=SpendCategory.APPAREL, amount=Decimal("1000"))
        self.assertEqual(picks[0].card.name, "Everyday")

    def test_a_brand_rule_only_applies_at_that_brand(self):
        at_shell = rank_cards(category=SpendCategory.FUEL, brand="Shell",
                              amount=Decimal("1000"))
        at_petron = rank_cards(category=SpendCategory.FUEL, brand="Petron",
                               amount=Decimal("1000"))

        self.assertEqual(
            {p.card.name: p.value for p in at_shell}["Shell card"], Decimal("30.00")
        )
        self.assertEqual(
            {p.card.name: p.value for p in at_petron}["Shell card"], Decimal("0.00")
        )

    def test_brand_matching_ignores_case(self):
        picks = rank_cards(brand="SHELL", amount=Decimal("1000"))
        self.assertEqual({p.card.name: p.value for p in picks}["Shell card"],
                         Decimal("30.00"))

    def test_cards_that_earn_nothing_are_still_listed(self):
        # Knowing a card earns nothing here is as useful as knowing another
        # earns 5%; dropping it leaves you wondering if it was considered.
        picks = rank_cards(category=SpendCategory.FUEL, brand="Petron",
                           amount=Decimal("1000"))
        self.assertEqual(len(picks), 3)
        self.assertEqual(picks[-1].value, Decimal("0.00"))

    def test_a_tie_goes_to_the_more_specific_rule(self):
        card = make_card("Both")
        add_reward(card, "4", category=SpendCategory.DINING)
        brand_rule = add_reward(card, "4", brand="Jollibee")

        picks = rank_cards(category=SpendCategory.DINING, brand="Jollibee",
                           amount=Decimal("1000"))
        chosen = {p.card.name: p for p in picks}["Both"]
        self.assertEqual(chosen.reward, brand_rule)

    def test_an_expired_rule_never_wins(self):
        lapsed = make_card("Lapsed")
        add_reward(lapsed, "10", category=SpendCategory.FUEL,
                   valid_to=timezone.localdate() - timedelta(days=1))

        picks = rank_cards(category=SpendCategory.FUEL, amount=Decimal("1000"))
        self.assertEqual(picks[0].card.name, "Fuel card")
        self.assertEqual({p.card.name: p.value for p in picks}["Lapsed"],
                         Decimal("0.00"))

    def test_a_retired_card_is_left_out(self):
        make_card("Old", is_active=False)
        picks = rank_cards(category=SpendCategory.FUEL)
        self.assertNotIn("Old", [p.card.name for p in picks])

    def test_the_effective_rate_reflects_a_cap(self):
        capped = make_card("Capped")
        add_reward(capped, "10", category=SpendCategory.FUEL,
                   monthly_cap=Decimal("100"))

        picks = rank_cards(category=SpendCategory.FUEL, amount=Decimal("10000"))
        chosen = {p.card.name: p for p in picks}["Capped"]
        # 10% of 10,000 is 1,000, but the cap makes the real rate 1%.
        self.assertEqual(chosen.value, Decimal("100.00"))
        self.assertEqual(chosen.effective_rate, Decimal("1.00"))

    def test_a_purchase_below_the_minimum_says_why(self):
        thresholded = make_card("Threshold")
        add_reward(thresholded, "5", category=SpendCategory.DINING,
                   min_spend=Decimal("1000"))

        picks = rank_cards(category=SpendCategory.DINING, amount=Decimal("200"))
        chosen = {p.card.name: p for p in picks}["Threshold"]
        self.assertIn("minimum", chosen.caveat)


class CategoryMappingTests(TestCase):
    def test_a_place_kind_maps_to_where_the_money_goes(self):
        self.assertEqual(category_for_place("fuel"), SpendCategory.FUEL)
        self.assertEqual(category_for_place("supermarket"), SpendCategory.GROCERY)
        self.assertEqual(category_for_place("fast_food"), SpendCategory.DINING)
        self.assertEqual(category_for_place("mall"), SpendCategory.APPAREL)
        # Pharmacies bill as health, which is how issuers categorise them.
        self.assertEqual(category_for_place("pharmacy"), SpendCategory.HEALTH)

    def test_every_place_kind_has_a_category(self):
        for kind in PlaceKind:
            self.assertIn(
                category_for_place(kind.value), SpendCategory.values, msg=kind.value
            )


class WalletSummaryTests(TestCase):
    def test_uncovered_categories_are_reported_as_gaps(self):
        card = make_card("Fuel only")
        add_reward(card, "5", category=SpendCategory.FUEL)

        gaps = {g["category"]: g for g in coverage_gaps()}
        self.assertTrue(gaps["fuel"]["covered"])
        self.assertFalse(gaps["apparel"]["covered"])

    def test_a_base_rate_card_covers_every_category(self):
        card = make_card("Base")
        add_reward(card, "1")

        self.assertTrue(all(g["covered"] for g in coverage_gaps()))

    def test_expiring_rules_are_listed_soonest_first(self):
        card = make_card("Promo")
        today = timezone.localdate()
        add_reward(card, "5", valid_to=today + timedelta(days=25))
        add_reward(card, "4", valid_to=today + timedelta(days=5))
        add_reward(card, "3", valid_to=today + timedelta(days=200))

        expiring = expiring_rewards(within_days=30)
        self.assertEqual([r.rate for r in expiring],
                         [Decimal("4.000"), Decimal("5.000")])

    def test_the_summary_counts_only_live_rules(self):
        card = make_card("Mixed")
        add_reward(card, "5")
        add_reward(card, "9", valid_to=timezone.localdate() - timedelta(days=1))

        summary = wallet_summary()
        self.assertEqual(summary["rules"], 1)
        self.assertEqual(summary["best_rate"], Decimal("5.000"))


class CardScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("spender", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_screens_render_with_no_cards(self):
        for name in ("cards:wallet", "cards:which", "cards:card_create"):
            with self.subTest(screen=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_screens_render_with_a_card(self):
        card = make_card("BPI Gold")
        add_reward(card, "5", category=SpendCategory.FUEL)

        for url in (
            reverse("cards:wallet"),
            reverse("cards:which"),
            reverse("cards:card_detail", args=[card.pk]),
            reverse("cards:card_edit", args=[card.pk]),
            reverse("cards:reward_create", args=[card.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_asking_about_a_place_fills_in_both_answers(self):
        card = make_card("Grocery card")
        add_reward(card, "4", category=SpendCategory.GROCERY)
        place = Place.objects.create(
            kind=PlaceKind.SUPERMARKET, osm_type=Place.OSMType.NODE, osm_id=1,
            name="Puregold Pasig", brand="Puregold",
            latitude=Decimal("14.58"), longitude=Decimal("121.06"),
        )

        response = self.client.get(reverse("cards:which"), {"place": place.pk})
        self.assertEqual(response.status_code, 200)
        # The place answers the category and the brand at once, which is the
        # point of arriving here from the map.
        self.assertEqual(response.context["category"], SpendCategory.GROCERY)
        self.assertEqual(response.context["brand"], "Puregold")
        self.assertEqual(response.context["winner"].card.name, "Grocery card")

    def test_a_nonsense_amount_falls_back_rather_than_erroring(self):
        response = self.client.get(reverse("cards:which"), {"amount": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.context["amount"], 0)

    def test_the_full_card_number_is_refused(self):
        response = self.client.post(reverse("cards:card_create"), {
            "name": "Test", "network": "visa", "last_four": "4111111111111111",
            "due_days_after_statement": 20, "annual_fee": "0", "is_active": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Card.objects.exists())

    def test_signed_out_users_reach_nothing(self):
        self.client.logout()
        response = self.client.get(reverse("cards:wallet"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
