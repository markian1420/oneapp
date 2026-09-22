"""
Tests for product tracking and seller trust.

The property that matters is that the app never makes a suspicious seller look
safe. It grades only what it can verify, warns on lookalike domains, and refuses
to let a cheap unverified listing outrank the brand's own store.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .models import Product, ProductPrice
from .products import (
    Quote,
    best_trusted,
    cheapest_overall,
    classify,
    domain_of,
    lookalike_warning,
    rank_quotes,
)


def quote(seller, price, trust, in_stock=True, url="") -> Quote:
    return Quote(
        seller=seller, url=url, price=Decimal(price), trust=trust,
        seen_on=timezone.localdate(), in_stock=in_stock,
    )


class DomainTests(TestCase):
    def test_the_host_is_extracted_from_any_shape_of_url(self):
        self.assertEqual(domain_of("https://ph.salomon.com/products/xt-6"),
                         "ph.salomon.com")
        self.assertEqual(domain_of("www.shopee.ph/thing"), "shopee.ph")
        self.assertEqual(domain_of(""), "")

    def test_the_brands_own_domain_is_recognised(self):
        self.assertEqual(
            classify("https://ph.salomon.com/products/xt-6-gore-tex", "Salomon"),
            "official",
        )

    def test_a_marketplace_is_recognised(self):
        self.assertEqual(
            classify("https://shopee.ph/salomon-xt6", "Salomon"), "marketplace"
        )

    def test_anything_else_starts_unverified(self):
        # A reassuring badge on a seller nobody checked is worse than none.
        self.assertEqual(
            classify("https://sneakerstore.ph/salomon", "Salomon"), "unverified"
        )

    def test_an_unknown_brand_does_not_promote_anyone(self):
        self.assertEqual(
            classify("https://randomshop.ph/thing", "Obscure Brand"), "unverified"
        )


class LookalikeTests(TestCase):
    def test_the_real_case_this_exists_for(self):
        # Searching "Salomon XT-6 Philippines" returns ph.salomon.com and
        # salomophilippines.com together. One is the brand.
        warning = lookalike_warning("https://www.salomophilippines.com/x", "Salomon")

        self.assertIsNotNone(warning)
        self.assertEqual(warning.domain, "salomophilippines.com")
        self.assertIn("near-miss", warning.detail)

    def test_the_official_domain_is_never_flagged(self):
        self.assertIsNone(lookalike_warning("https://ph.salomon.com/x", "Salomon"))

    def test_a_marketplace_is_not_flagged_as_a_lookalike(self):
        # Shopee is not pretending to be Salomon; its risk is different and is
        # carried by its trust tier instead.
        self.assertIsNone(lookalike_warning("https://shopee.ph/salomon", "Salomon"))

    def test_a_shop_using_the_brand_name_gets_the_softer_note(self):
        warning = lookalike_warning("https://salomon-outlet.ph/x", "Salomon")
        self.assertIsNotNone(warning)
        self.assertIn("normal for a stockist", warning.detail)

    def test_an_unrelated_domain_is_left_alone(self):
        self.assertIsNone(lookalike_warning("https://toby's.ph/x", "Salomon"))
        self.assertIsNone(lookalike_warning("https://commonwealth-ftgg.ph/x", "Salomon"))


class RankingTests(TestCase):
    def test_trust_groups_before_price_sorts(self):
        quotes = [
            quote("Dodgy Deals", "5999", "unverified"),
            quote("Salomon PH", "12990", "official"),
            quote("Shopee seller", "7500", "marketplace"),
        ]
        ranked = rank_quotes(quotes)

        # Sorting purely on price would put the 5,999 listing on top, which
        # reads as a recommendation to buy the suspicious one.
        self.assertEqual([q.seller for q in ranked],
                         ["Salomon PH", "Shopee seller", "Dodgy Deals"])

    def test_price_still_decides_within_a_tier(self):
        quotes = [
            quote("Stockist B", "13500", "authorised"),
            quote("Stockist A", "12000", "authorised"),
        ]
        self.assertEqual([q.seller for q in rank_quotes(quotes)],
                         ["Stockist A", "Stockist B"])

    def test_the_cheapest_trusted_ignores_untrusted_bargains(self):
        quotes = [
            quote("Dodgy Deals", "5999", "unverified"),
            quote("Salomon PH", "12990", "official"),
        ]
        self.assertEqual(best_trusted(quotes).seller, "Salomon PH")

    def test_out_of_stock_is_not_offered_as_the_answer(self):
        quotes = [
            quote("Salomon PH", "12990", "official", in_stock=False),
            quote("Stockist", "13500", "authorised"),
        ]
        self.assertEqual(best_trusted(quotes).seller, "Stockist")

    def test_with_nothing_trusted_it_says_so_rather_than_promoting_one(self):
        quotes = [quote("Dodgy Deals", "5999", "unverified")]

        self.assertIsNone(best_trusted(quotes))
        self.assertEqual(cheapest_overall(quotes).seller, "Dodgy Deals")


class ProductModelTests(TestCase):
    def test_the_label_reads_as_the_thing_you_searched_for(self):
        product = Product.objects.create(
            brand="Salomon", model="XT-6", variant="Gore-Tex"
        )
        self.assertEqual(product.label, "Salomon XT-6 Gore-Tex")

    def test_a_price_older_than_a_month_is_stale(self):
        product = Product.objects.create(brand="Salomon", model="XT-6")
        old = ProductPrice.objects.create(
            product=product, seller="Somewhere", price=Decimal("12990"),
            seen_on=timezone.localdate() - timedelta(days=45),
        )
        fresh = ProductPrice.objects.create(
            product=product, seller="Elsewhere", price=Decimal("12990"),
        )
        self.assertTrue(old.is_stale)
        self.assertFalse(fresh.is_stale)
