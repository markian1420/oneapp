"""
Tests for the bank promo importer.

Built on a small synthetic payload rather than the real page, which is 12MB and
changes daily. The shapes here are copied from records actually published by
Metrobank on 18 August 2026.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.core.categories import SpendCategory
from apps.places.models import Place, PlaceKind

from .banks import BankPromo, parse_metrobank
from .models import Promo

STANDARD_CARDS = [
    {"title": "Titanium Mastercard"}, {"title": "Platinum Mastercard"},
    {"title": "Toyota Platinum"}, {"title": "Toyota Mastercard"},
]


def payload(records) -> str:
    body = {"props": {"pageProps": {"data": {"promoDetails": records}}}}
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(body)
        + "</script></body></html>"
    )


def record(**overrides) -> dict:
    base = {
        "title": "50% OFF at Domino's Pizza",
        "description": "Get this deal on select medium and family-sized pizzas.",
        "slug": "/discounts-dominos-pizza",
        "startDate": "2026-08-01T00:00:00+08:00",
        "expirationDate": "2026-10-31T00:00:00+08:00",
        "cards": STANDARD_CARDS,
        "categories": [{"category": "Dining"}],
    }
    base.update(overrides)
    return base


class ParsingTests(TestCase):
    def test_a_record_becomes_a_promo_with_its_dates(self):
        promo = parse_metrobank(payload([record()]), base_url="https://x.test")[0]

        self.assertEqual(promo.title, "50% OFF at Domino's Pizza")
        self.assertEqual(promo.discount_pct, Decimal("50"))
        self.assertEqual(promo.starts_on, date(2026, 8, 1))
        # The end date is the whole reason this source is worth having.
        self.assertEqual(promo.ends_on, date(2026, 10, 31))
        self.assertEqual(promo.source_url, "https://x.test/discounts-dominos-pizza")

    def test_the_merchant_is_taken_from_the_title(self):
        promo = parse_metrobank(payload([record()]), base_url="")[0]
        self.assertEqual(promo.brand, "Domino's Pizza")

    def test_a_situation_is_not_mistaken_for_a_brand(self):
        # "at select restaurants in Hong Kong" names a situation, not a chain.
        # Blank is the right answer; a bogus brand is unmatchable and misleading.
        for title in (
            "Up to 20% OFF at select restaurants in Hong Kong",
            "10% OFF at participating stores",
            "5% OFF at any branch nationwide",
        ):
            with self.subTest(title=title):
                promo = parse_metrobank(payload([record(title=title)]), base_url="")[0]
                self.assertEqual(promo.brand, "")

    def test_a_leading_article_is_dropped_from_the_brand(self):
        promo = parse_metrobank(
            payload([record(title="EXTRA 5% OFF at the SM Shoes and Bags Sale")]),
            base_url="",
        )[0]
        self.assertFalse(promo.brand.lower().startswith("the "))

    def test_percentages_and_fixed_prices_are_both_read(self):
        percent = parse_metrobank(payload([record(title="35% OFF at LAC")]), base_url="")[0]
        self.assertEqual(percent.discount_pct, Decimal("35"))

        fixed = parse_metrobank(
            payload([record(title="Lunch for P499 at Vikings")]), base_url=""
        )[0]
        self.assertIsNone(fixed.discount_pct)
        self.assertEqual(fixed.price, Decimal("499"))

    def test_a_full_card_list_reads_as_any_card(self):
        # Naming all four is noise; blank means "any card from this issuer".
        promo = parse_metrobank(payload([record()]), base_url="")[0]
        self.assertEqual(promo.card_name, "")

    def test_a_short_card_list_is_kept(self):
        promo = parse_metrobank(
            payload([record(cards=[{"title": "Platinum Mastercard"}])]), base_url=""
        )[0]
        self.assertEqual(promo.card_name, "Platinum Mastercard")

    def test_invisible_characters_are_stripped(self):
        # Zero-width spaces survive strip() and break both the console and any
        # attempt to match the title again on re-import.
        promo = parse_metrobank(
            payload([record(title="20%​ OFF at Vara﻿ Dining")]), base_url=""
        )[0]
        self.assertEqual(promo.title, "20% OFF at Vara Dining")

    def test_a_missing_payload_is_a_clear_error(self):
        with self.assertRaises(ValueError):
            parse_metrobank("<html><body>nothing here</body></html>", base_url="")

    def test_an_unexpected_payload_shape_is_a_clear_error(self):
        body = '<script id="__NEXT_DATA__">{"props":{}}</script>'
        with self.assertRaises(ValueError):
            parse_metrobank(body, base_url="")


class CategoryTests(TestCase):
    def test_the_bank_category_is_used_by_default(self):
        promo = parse_metrobank(
            payload([record(title="30% OFF at Somewhere Unknown",
                            categories=[{"category": "Travel"}])]),
            base_url="",
        )[0]
        self.assertEqual(promo.category, SpendCategory.TRANSPORT)

    def test_a_brand_on_the_map_overrides_the_bank_category(self):
        # Metrobank files Domino's under Shopping; the app knows it is fast
        # food, and dining is the more useful answer.
        Place.objects.create(
            kind=PlaceKind.FAST_FOOD, osm_type=Place.OSMType.NODE, osm_id=1,
            name="Domino's", brand="Domino's Pizza",
            latitude=Decimal("14.58"), longitude=Decimal("121.06"),
        )
        promo = parse_metrobank(
            payload([record(categories=[{"category": "Shopping"}])]), base_url=""
        )[0]
        self.assertEqual(promo.category, SpendCategory.DINING)

    def test_an_unknown_category_falls_back_rather_than_failing(self):
        promo = parse_metrobank(
            payload([record(title="Deal", categories=[{"category": "Astrology"}])]),
            base_url="",
        )[0]
        self.assertEqual(promo.category, SpendCategory.OTHER)


class ImportCommandTests(TestCase):
    def _run(self, promos, **options):
        out = StringIO()
        with mock.patch("apps.spend.management.commands.import_bank_promos.fetch",
                        return_value=promos):
            call_command("import_bank_promos", stdout=out, stderr=StringIO(), **options)
        return out.getvalue()

    def _promo(self, **overrides) -> BankPromo:
        values = {
            "reference": "/deal", "title": "50% OFF at Domino's Pizza",
            "detail": "", "brand": "Domino's Pizza",
            "category": SpendCategory.DINING, "card_name": "",
            "discount_pct": Decimal("50"), "price": None,
            "starts_on": timezone.localdate(),
            "ends_on": timezone.localdate() + timedelta(days=30),
            "source_url": "https://x.test/deal",
        }
        values.update(overrides)
        return BankPromo(**values)

    def test_promos_land_with_the_issuer_recorded(self):
        self._run([self._promo()])

        promo = Promo.objects.get()
        self.assertEqual(promo.issuer, "Metrobank")
        self.assertTrue(promo.is_card_promo)
        self.assertEqual(promo.qualifies, "Any Metrobank card")

    def test_expired_promos_are_left_out_by_default(self):
        self._run([
            self._promo(reference="/live"),
            self._promo(reference="/old",
                        ends_on=timezone.localdate() - timedelta(days=1)),
        ])
        self.assertEqual(
            [p.source_ref for p in Promo.objects.all()], ["/live"]
        )

    def test_expired_promos_can_be_kept_deliberately(self):
        self._run([
            self._promo(reference="/old",
                        ends_on=timezone.localdate() - timedelta(days=1)),
        ], include_expired=True)
        self.assertEqual(Promo.objects.count(), 1)

    def test_a_promo_with_no_end_date_is_dropped_as_a_parse_failure(self):
        # Every record from this source carries an expiry, so a missing one is
        # a parsing problem, not an offer that runs for ever.
        output = self._run([self._promo(ends_on=None)])

        self.assertEqual(Promo.objects.count(), 0)
        self.assertIn("no end date", output)

    def test_re_importing_updates_in_place(self):
        self._run([self._promo()])
        self._run([self._promo(title="60% OFF at Domino's Pizza")])

        self.assertEqual(Promo.objects.count(), 1)
        self.assertEqual(Promo.objects.get().title, "60% OFF at Domino's Pizza")

    def test_a_dry_run_writes_nothing(self):
        output = self._run([self._promo()], dry_run=True)

        self.assertEqual(Promo.objects.count(), 0)
        self.assertIn("Dry run", output)

    def test_an_unknown_bank_is_refused(self):
        with self.assertRaises(CommandError):
            call_command("import_bank_promos", bank="notabank", stdout=StringIO())

    def test_a_failed_fetch_stops_with_a_clear_message(self):
        with mock.patch("apps.spend.management.commands.import_bank_promos.fetch",
                        side_effect=ValueError("page structure changed")):
            with self.assertRaises(CommandError):
                call_command("import_bank_promos", stdout=StringIO(), stderr=StringIO())

    def test_importing_stores_no_card_of_yours(self):
        self._run([self._promo(card_name="Platinum Mastercard")])

        # card_name says which card *qualifies* - a fact about the offer. The
        # app still has nowhere to record a card you hold.
        from django.apps import apps
        names = {m.__name__.lower() for m in apps.get_models()}
        self.assertNotIn("card", names)
        self.assertEqual(Promo.objects.get().card_name, "Platinum Mastercard")
