"""
Tests for the DA Daily Price Index parser.

Built on synthetic word geometry rather than a checked-in PDF: the parser's
whole job is turning coordinates into columns, so coordinates are the honest
input to test it with, and a 380KB binary fixture in the repository would make
the failure mode ("which of these 1,300 words moved?") much harder to read.

The layouts encoded here are measured from the real published editions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from .da_index import ROW_TOLERANCE, daily_index_url, parse
from .models import Commodity, CommodityCategory, CommodityPrice

# Measured from the published editions: rows are pitched 20.76pt apart, the
# commodity column starts at x=78, specification at x=268, price at x=482.
ROW_PITCH = 20.76
COMMODITY_X = 78.0
SPEC_X = 268.0
PRICE_X = 482.0


def word(text: str, x0: float, top: float) -> dict:
    return {"text": text, "x0": x0, "x1": x0 + 6 * len(text),
            "top": top, "bottom": top + 12.0}


class FakePage:
    def __init__(self, words):
        self._words = words

    def extract_words(self):
        return self._words


class FakePDF:
    def __init__(self, pages):
        self.pages = pages


def build_page(rows, *, price_offset: float = 0.0, start_top: float = 100.0):
    """Lay out (commodity, spec, price) triples as positioned words.

    ``price_offset`` shifts the price cell's baseline relative to its row,
    which is exactly what differed between the 5 and 17 August editions.
    """
    words = []
    for index, (name, spec, price) in enumerate(rows):
        top = start_top + index * ROW_PITCH
        for part, x in ((name, COMMODITY_X), (spec, SPEC_X)):
            offset = 0.0
            for token in part.split():
                words.append(word(token, x + offset, top))
                offset += 6 * len(token) + 3
        if price:
            words.append(word(price, PRICE_X, top + price_offset))
    return FakePage(words)


TITLE = [("Department of Agriculture", "", ""),
         ("DAILY PRICE INDEX", "", ""),
         ("(Monday, August 17, 2026)", "", "")]


class ColumnParsingTests(TestCase):
    def test_columns_split_by_geometry_not_by_guessing(self):
        # The exact row that defeats a regex: the brand belongs to the
        # specification column, not to the commodity name.
        pdf = FakePDF([build_page(TITLE + [
            ("POULTRY PRODUCTS", "", ""),
            ("Chicken Breast, Local", "Magnolia", "222.68"),
            ("Chicken Breast, Local", "Unbranded, Fresh", "215.80"),
        ])])
        result = parse(pdf)

        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.rows[0].name, "Chicken Breast, Local")
        self.assertEqual(result.rows[0].specification, "Magnolia")
        self.assertEqual(result.rows[0].price, Decimal("222.68"))
        self.assertEqual(result.rows[0].category, CommodityCategory.POULTRY)

    def test_the_published_date_is_read_from_the_title(self):
        result = parse(FakePDF([build_page(TITLE + [("LEGUMES", "", ""),
                                                    ("Mungbean", "", "147.48")])]))
        self.assertEqual(result.published_on, date(2026, 8, 17))

    def test_section_headings_set_the_category(self):
        pdf = FakePDF([build_page(TITLE + [
            ("PORK MEAT PRODUCTS", "", ""),
            ("Pork Belly (Liempo), Local", "", "381.89"),
            ("FRUITS", "", ""),
            ("Banana (Lakatan)", "", "110.00"),
        ])])
        rows = parse(pdf).rows
        self.assertEqual(rows[0].category, CommodityCategory.PORK)
        self.assertEqual(rows[1].category, CommodityCategory.FRUITS)


class PriceCellTests(TestCase):
    def test_n_a_is_recorded_as_unavailable_not_as_zero(self):
        pdf = FakePDF([build_page(TITLE + [
            ("LOCAL COMMERCIAL RICE", "", ""),
            ("Regular Milled", "20-40% bran streak", "n/a"),
            ("Well Milled", "1-19% bran streak", "48.53"),
        ])])
        result = parse(pdf)

        # A commodity nobody stocked is not a commodity that cost nothing.
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.unavailable, 1)
        self.assertEqual(result.dropped, 0)

    def test_thousands_separators_are_handled(self):
        pdf = FakePDF([build_page(TITLE + [
            ("OTHER BASIC COMMODITIES", "", ""),
            ("Imported Rice", "premium sack", "1,250.00"),
        ])])
        self.assertEqual(parse(pdf).rows[0].price, Decimal("1250.00"))


class LayoutDriftTests(TestCase):
    """Regression cover for the bug that lost 18 rows on 5 August 2026."""

    ROWS = TITLE + [
        ("OTHER BASIC COMMODITIES", "", ""),
        ("Sugar (Brown)", "", "72.29"),
        ("Cooking Oil (Palm)", "350 ml/bottle", "39.70"),
        ("Cooking Oil (Palm)", "1 Liter/bottle", "100.57"),
    ]

    def test_a_price_on_the_same_baseline_parses(self):
        result = parse(FakePDF([build_page(self.ROWS, price_offset=0.0)]))
        self.assertEqual(len(result.rows), 3)
        self.assertEqual(result.dropped, 0)

    def test_a_price_set_above_its_row_still_parses(self):
        # The 5 August edition sets the price cell 3.12pt higher than the
        # commodity. At the original 3.0pt tolerance this split every row in
        # two and silently dropped the lot.
        result = parse(FakePDF([build_page(self.ROWS, price_offset=-3.12)]))

        self.assertEqual(len(result.rows), 3)
        self.assertEqual(result.dropped, 0)
        self.assertEqual(result.rows[0].name, "Sugar (Brown)")
        self.assertEqual(result.rows[0].price, Decimal("72.29"))

    def test_the_tolerance_stays_well_inside_the_row_pitch(self):
        # If tolerance ever grows past half the pitch, adjacent rows merge and
        # prices attach to the wrong commodity - a far worse failure than
        # dropping them, because it is invisible.
        self.assertLess(ROW_TOLERANCE, ROW_PITCH / 2)
        self.assertGreater(ROW_TOLERANCE, 3.12)


class WrappedCellTests(TestCase):
    def test_a_name_wrapping_around_its_price_is_rejoined(self):
        # Seen verbatim in the published editions: the commodity name starts
        # above the priced line and finishes below it.
        pdf = FakePDF([build_page(TITLE + [
            ("OTHER BASIC COMMODITIES", "", ""),
            ("Cooking Oil (Palm Olein, Jolly", "", ""),
            ("", "1,000 ml/bottle", "154.00"),
            ("Brand)", "", ""),
        ])])
        result = parse(pdf)

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].name, "Cooking Oil (Palm Olein, Jolly Brand)")
        self.assertEqual(result.rows[0].specification, "1,000 ml/bottle")

    def test_a_following_commodity_is_not_swallowed_as_a_tail(self):
        pdf = FakePDF([build_page(TITLE + [
            ("FRUITS", "", ""),
            ("Banana (Lakatan)", "", "110.00"),
            ("Mango (Carabao)", "", "180.00"),
        ])])
        rows = parse(pdf).rows
        self.assertEqual([r.name for r in rows],
                         ["Banana (Lakatan)", "Mango (Carabao)"])


class FooterTests(TestCase):
    def test_parsing_stops_at_the_methodology_notes(self):
        pdf = FakePDF([build_page(TITLE + [
            ("FRUITS", "", ""),
            ("Banana (Lakatan)", "", "110.00"),
            ("Note(s):", "", ""),
            ("a) Prevailing price is defined as", "the price", "in a given area."),
            ("1. Agora Public Market", "", ""),
        ])])
        result = parse(pdf)

        # The market list and the methodology prose are not commodities.
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].name, "Banana (Lakatan)")

    def test_repeating_page_furniture_is_ignored(self):
        pdf = FakePDF([build_page(TITLE + [
            ("COMMODITY SPECIFICATION", "", "RETAIL PRICE PER"),
            ("FRUITS", "", ""),
            ("Banana (Lakatan)", "", "110.00"),
            ("Page 1 of 8", "", ""),
        ])])
        self.assertEqual(len(parse(pdf).rows), 1)


class UrlTests(TestCase):
    def test_the_published_url_matches_the_da_naming(self):
        self.assertEqual(
            daily_index_url(date(2026, 8, 17)),
            "https://www.da.gov.ph/wp-content/uploads/2026/08/"
            "Daily-Price-Index-August-17-2026.pdf",
        )

    def test_the_day_is_not_zero_padded(self):
        # The DA writes "August-5-2026", not "August-05-2026"; padding it
        # produces a 404 on every single-digit day of the month.
        self.assertIn("August-5-2026", daily_index_url(date(2026, 8, 5)))


class OrphanPruningTests(TestCase):
    def test_a_commodity_with_no_prices_is_pruned_on_import(self):
        stranded = Commodity.objects.create(
            category=CommodityCategory.PORK, name="Left over from an old parser"
        )
        kept = Commodity.objects.create(
            category=CommodityCategory.PORK, name="Pork Belly (Liempo), Local"
        )
        CommodityPrice.objects.create(
            commodity=kept, observed_on=date(2026, 8, 17), price=Decimal("381.89")
        )

        # Weekend dates publish nothing, so this exercises the pruning pass
        # without reaching the network.
        call_command("import_da_prices", date="2026-08-15")

        self.assertTrue(Commodity.objects.filter(pk=kept.pk).exists())
        self.assertFalse(Commodity.objects.filter(pk=stranded.pk).exists())
