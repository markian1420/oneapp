"""
Tests for the readers added for the other Philippine issuers.

The fixtures are cut down from the real pages as published on 18 August 2026 -
the same class names and the same date wording, with the markup around them
thrown away. Full pages run to hundreds of kilobytes and change daily, so
copying one in would test nothing except that today's copy still parses.

What these defend is narrow and specific: every bank writes its promo period
differently, and getting the end date wrong is the one mistake that makes the
whole screen worse than useless.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from apps.core.categories import SpendCategory
from apps.places.models import Place, PlaceKind

from .banks import BY_KEY, ISSUERS, SOURCES, bankcom, bpi, eastwest, maya, rcbc
from .banks.base import merchant_from, offer_from, promo_dates
from .models import Promo


class PromoDateTests(SimpleTestCase):
    """The one field that decides whether a promo is worth showing."""

    def test_a_range_that_states_the_year_once_at_the_end(self):
        # RCBC: "August 15 - December 31, 2026"
        self.assertEqual(
            promo_dates("August 15 - December 31, 2026"),
            (date(2026, 8, 15), date(2026, 12, 31)),
        )

    def test_a_range_written_with_to(self):
        # BankCom: "Promo Period: August 17 to December 18, 2026"
        self.assertEqual(
            promo_dates("August 17 to December 18, 2026"),
            (date(2026, 8, 17), date(2026, 12, 18)),
        )

    def test_a_lone_date_is_read_as_the_end(self):
        # BPI: "Valid until Jul 14, 2027". Guessing this is a start date would
        # show an ended promo as running.
        self.assertEqual(
            promo_dates("Valid until Jul 14, 2027"), (None, date(2027, 7, 14))
        )

    def test_a_range_crossing_new_year_does_not_run_backwards(self):
        self.assertEqual(
            promo_dates("December 15 - January 31, 2027"),
            (date(2026, 12, 15), date(2027, 1, 31)),
        )

    def test_the_first_text_with_dates_wins(self):
        """Callers pass their candidates most-specific first."""
        self.assertEqual(
            promo_dates("", "Until August 31, 2026", "some prose from July 4, 2019"),
            (None, date(2026, 8, 31)),
        )

    def test_an_impossible_date_is_a_parse_failure_not_a_promo(self):
        self.assertEqual(promo_dates("February 31, 2026"), (None, None))

    def test_text_with_no_dates_says_so(self):
        self.assertEqual(promo_dates("Promo period: while stocks last"), (None, None))


class MerchantTests(SimpleTestCase):
    def test_a_merchant_after_with_or_by(self):
        self.assertEqual(
            merchant_from("VYBE into Bold Flavors with Propaganda Bistro"),
            "Propaganda Bistro",
        )
        self.assertEqual(
            merchant_from("La Terraza by Alta D' Tagaytay Hotel Promo"),
            "Alta D' Tagaytay Hotel",
        )

    def test_a_payment_method_is_never_the_merchant(self):
        # "with your EastWest credit card" reads exactly like a merchant to a
        # regex, and putting it in the brand column would be nonsense.
        self.assertEqual(
            merchant_from("Get P700 Cashback with your EastWest Visa Credit Card"), ""
        )

    def test_the_offer_itself_is_never_the_merchant(self):
        self.assertEqual(merchant_from("Buy now, pay later with 0% installment"), "")
        self.assertEqual(merchant_from("No Annual Fee For Life with waived fees"), "")

    def test_a_capitalised_article_is_part_of_the_name(self):
        # "A Lounge" is a bar. Stripping the "A" leaves a different business.
        self.assertEqual(merchant_from("Up to 58% OFF at A Lounge"), "A Lounge")

    def test_a_trailing_clause_is_trimmed(self):
        self.assertEqual(
            merchant_from("50% OFF at Shakey's, exclusively for cardholders"),
            "Shakey's",
        )


BPI_HTML = """
<div class="article-page-cont cardView">
  <p class="tab-name-cont">Credit cards</p>
  <p class="tab-date-cont">Valid until Jul 14, 2027</p>
  <p class="tab-head-cont">Acacia Hotel Davao Promo</p>
  <p class="article-desc">Enjoy 15% off on a la carte orders with your BPI Card.</p>
  <p class="view-article-link">
    <a href="/personal/rewards-and-promotions/promos/acacia-hotel-davao-promo">View details</a>
  </p>
</div>
<div class="article-page-cont listView">
  <p class="tab-date-cont">Valid until Jul 14, 2027</p>
  <p class="tab-head-cont">Acacia Hotel Davao Promo</p>
  <p class="article-desc">Enjoy 15% off on a la carte orders with your BPI Card.</p>
  <p class="view-article-link">
    <a href="/personal/rewards-and-promotions/promos/acacia-hotel-davao-promo">View details</a>
  </p>
</div>
"""


class BPITests(TestCase):
    def parse(self):
        return bpi.parse(BPI_HTML, base_url="https://www.bpi.com.ph")

    def test_the_listing_carries_its_own_end_date(self):
        promo = self.parse()[0]
        self.assertEqual(promo.ends_on, date(2027, 7, 14))
        self.assertEqual(promo.title, "Acacia Hotel Davao Promo")
        self.assertEqual(promo.discount_pct, Decimal("15"))

    def test_the_same_promo_rendered_twice_is_read_once(self):
        """BPI ships every promo in both a card view and a list view."""
        self.assertEqual(len(self.parse()), 1)

    def test_a_promo_named_after_its_merchant_yields_that_merchant(self):
        self.assertEqual(self.parse()[0].brand, "Acacia Hotel Davao")


EASTWEST_HTML = """
<div class="card promo-card">
  <div class="card-img"><a href="/promos/15-savings-serenitea"><img></a></div>
  <div class="card-content">
    <a href="/promos/15-savings-serenitea"><h3>15% savings at Serenitea</h3></a>
    <p>August 15 to September 30, 2026</p>
    <div class="excerpt"><p>Enjoy 15% savings for a minimum spend of Php 550.</p></div>
  </div>
  <div class="card-footer"><div class="pills-categories">
    <span class="pill-category pill-dining">Dining</span>
    <span class="pill-category pill-luzon">Luzon</span>
  </div></div>
</div>
"""


class EastWestTests(TestCase):
    def test_a_promo_card_yields_dates_brand_and_category(self):
        promo = eastwest.parse(
            EASTWEST_HTML, base_url="https://www.eastwestbanker.com")[0]

        self.assertEqual(promo.starts_on, date(2026, 8, 15))
        self.assertEqual(promo.ends_on, date(2026, 9, 30))
        self.assertEqual(promo.brand, "Serenitea")
        self.assertEqual(promo.category, SpendCategory.DINING)
        self.assertEqual(promo.discount_pct, Decimal("15"))

    def test_a_region_pill_is_not_a_category(self):
        """Luzon says where, not what. Left in, it would win the category."""
        promo = eastwest.parse(
            EASTWEST_HTML, base_url="https://www.eastwestbanker.com")[0]
        self.assertNotEqual(promo.category, SpendCategory.OTHER)

    def test_the_pager_stops_when_a_page_repeats_itself(self):
        pages = {
            "https://x.test/promos?page=0": EASTWEST_HTML,
            "https://x.test/promos?page=1": EASTWEST_HTML,
        }
        promos = eastwest.collect(
            {"url": "https://x.test/promos", "base": "https://x.test"},
            fetch_page=lambda url: pages.get(url, ""),
        )
        self.assertEqual(len(promos), 1)


BANKCOM_HTML = """
<article class="post-130476 promotions">
  <header class="entry-header"><h2 class="entry-title">
    <a href="https://www.bankcom.com.ph/promotions/premium-buffet/">
      Experience Premium Buffet at Makati Diamond Residences at 50% OFF
    </a>
  </h2></header>
  <div class="entry-content">
    Promo Mechanics BankCom x Makati Diamond Residences
    Promo Period: August 17 to December 18, 2026, from Monday to Friday
    Promo Offer: 50% OFF on Business&hellip;
    <a href="https://www.bankcom.com.ph/promotions/premium-buffet/" class="read-more">Read more</a>
  </div>
</article>
"""


class BankComTests(TestCase):
    def test_the_promo_period_is_read_from_the_excerpt(self):
        promo = bankcom.parse(BANKCOM_HTML, base_url="https://www.bankcom.com.ph")[0]

        self.assertEqual(promo.starts_on, date(2026, 8, 17))
        self.assertEqual(promo.ends_on, date(2026, 12, 18))
        self.assertEqual(promo.discount_pct, Decimal("50"))

    def test_the_read_more_link_is_not_part_of_the_description(self):
        promo = bankcom.parse(BANKCOM_HTML, base_url="https://www.bankcom.com.ph")[0]
        self.assertNotIn("Read more", promo.detail)


RCBC_LISTING = """
<div class="promolistdv"><div class="promolistmain">
  <div class="promodetailsdiv">
    <div class="title"><a href="/promos/IlPadrino30">30% OFF at Il Padrino</a></div>
    <div class="desc">Pair your favourite coffee with delicious food.</div>
  </div>
</div></div>
"""

# The site currently ships this div commented out, which is exactly why the
# reader looks at the raw HTML and keeps the prose as a second opinion.
RCBC_DETAIL = """
<div class="insideimghldr">
  <!-- <div class="datehldr">August 15 - December 31, 2026</div> -->
  <p>The promo period is from August 15 to December 31, 2026.</p>
</div>
"""


class RCBCTests(TestCase):
    def test_the_listing_gives_everything_except_the_dates(self):
        promo = rcbc.parse_listing(RCBC_LISTING, base_url="https://rcbccredit.com")[0]

        self.assertEqual(promo.brand, "Il Padrino")
        self.assertEqual(promo.discount_pct, Decimal("30"))
        self.assertIsNone(promo.ends_on)

    def test_a_date_inside_an_html_comment_is_still_read(self):
        self.assertEqual(
            rcbc.parse_detail(RCBC_DETAIL),
            (date(2026, 8, 15), date(2026, 12, 31)),
        )

    def test_the_prose_carries_the_dates_if_the_div_goes(self):
        without_div = "<p>The promo period is from August 15 to December 31, 2026.</p>"
        self.assertEqual(
            rcbc.parse_detail(without_div),
            (date(2026, 8, 15), date(2026, 12, 31)),
        )

    def test_an_unreachable_detail_page_does_not_lose_the_rest(self):
        """One 404 in 150 should cost one promo, not the whole import."""
        def fetch_page(url):
            if url == "https://rcbccredit.com/promos":
                return RCBC_LISTING
            raise OSError("connection reset")

        promos = rcbc.collect(
            {"url": "https://rcbccredit.com/promos", "base": "https://rcbccredit.com"},
            fetch_page=fetch_page, delay=0,
        )
        self.assertEqual(len(promos), 1)
        self.assertIsNone(promos[0].ends_on)


MAYA_HTML = """
<div class="featured_deals_card">
  <a href="https://www.maya.ph/deals/one-percent-cashback">
    <div class="mlr20">
      <small class="promo-ongoing">Until December 31, 2026</small>
      <p class="text_size_20">1% cashback ng Maya Card? Mine mo na yan!</p>
    </div>
  </a>
  <div class="tags-container"><span class="tag">Maya Card</span></div>
</div>
<a href="https://www.maya.ph/deals/archive">See past deals</a>
"""


class MayaTests(TestCase):
    def test_a_deal_card_yields_its_end_date(self):
        promo = maya.parse(MAYA_HTML, base_url="https://www.maya.ph")[0]

        self.assertEqual(promo.ends_on, date(2026, 12, 31))
        self.assertEqual(promo.reference, "one-percent-cashback")

    def test_a_navigation_link_is_not_a_deal(self):
        self.assertEqual(len(maya.parse(MAYA_HTML, base_url="https://www.maya.ph")), 1)


class RegistryTests(SimpleTestCase):
    def test_every_issuer_is_either_readable_or_says_why_not(self):
        """A missing bank and a blocked one look the same from outside."""
        for issuer in ISSUERS:
            with self.subTest(issuer=issuer.key):
                self.assertTrue(
                    issuer.readable or issuer.blocked,
                    f"{issuer.name} is neither readable nor explained",
                )

    def test_readable_issuers_are_exactly_the_importable_ones(self):
        self.assertEqual(
            {i.key for i in ISSUERS if i.readable}, set(SOURCES)
        )

    def test_the_banks_that_refuse_automated_readers_are_left_alone(self):
        # A 403 is the site owner's decision. Working around it would be the
        # wrong kind of clever.
        for key in ("bdo", "securitybank"):
            with self.subTest(bank=key):
                self.assertFalse(BY_KEY[key].readable)
                self.assertIn("403", BY_KEY[key].blocked)


class BlockedIssuerCommandTests(TestCase):
    def test_asking_for_a_blocked_bank_gives_its_reason(self):
        with self.assertRaises(CommandError) as caught:
            call_command("import_bank_promos", bank="bdo", stdout=StringIO())

        self.assertIn("403", str(caught.exception))

    def test_listing_shows_every_issuer(self):
        out = StringIO()
        call_command("import_bank_promos", list=True, stdout=out)
        output = out.getvalue()

        for issuer in ISSUERS:
            self.assertIn(issuer.name, output)

    def test_all_reads_every_readable_issuer_and_records_each(self):
        with mock.patch("apps.spend.management.commands.import_bank_promos.fetch",
                        side_effect=lambda bank: []):
            out = StringIO()
            call_command("import_bank_promos", bank="all", stdout=out)

        for key in SOURCES:
            self.assertIn(BY_KEY[key].name, out.getvalue())


class CategoryFromPlacesTests(TestCase):
    def test_a_brand_on_the_map_decides_the_category(self):
        """The bank's own label loses to what the shop actually is."""
        Place.objects.create(
            name="Serenitea Ortigas", brand="Serenitea", kind=PlaceKind.FAST_FOOD,
            osm_type="node", osm_id=1, latitude="14.580000", longitude="121.060000",
        )
        promo = eastwest.parse(
            EASTWEST_HTML, base_url="https://www.eastwestbanker.com")[0]

        self.assertEqual(promo.brand, "Serenitea")
        self.assertEqual(promo.category, SpendCategory.DINING)


class OfferTests(SimpleTestCase):
    def test_a_percentage_beats_a_price_in_the_same_sentence(self):
        self.assertEqual(
            offer_from("30% OFF, minimum spend of Php 3,000"),
            (Decimal("30"), None),
        )

    def test_a_fixed_price_is_read_when_there_is_no_percentage(self):
        self.assertEqual(
            offer_from("Ramen for Php 99 all day"), (None, Decimal("99"))
        )
