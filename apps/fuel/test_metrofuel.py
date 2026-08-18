"""
Tests for the MetroFuel Tracker parser.

The one that matters is the Cleanfuel case. The page lists it with 83 stations
and no prices at all, so a parser that simply takes the next two numbers after
a brand hands it the following brand's figures - and does so silently, which is
the worst kind of wrong.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .metrofuel import parse, to_text
from .models import DOEAdvisory, FuelType
from .services import week_start

# Shaped exactly like the real page, including a brand listed with stations but
# no published prices.
PAGE = """
<html><body>
<h1>Fuel Prices Today</h1>
<p><span>5626</span> stations · <span>153</span> cities · Updated <span>August 18, 2026</span></p>
<h2>National Average Prices</h2>
<div>Diesel <span>₱</span><span>90.52</span></div>
<div>Premium Diesel <span>₱</span><span>95.32</span></div>
<div>Unleaded 91 <span>₱</span><span>79.00</span></div>
<div>Premium 95 <span>₱</span><span>81.90</span></div>
<div>Premium 97/98/100 <span>₱</span><span>88.60</span></div>
<h2>Prices by Brand</h2>
<div><span>Petron</span><span>1017</span> stations
  <span>Avg Diesel </span><span>₱</span><span>89.92</span>
  <span>Avg Unleaded 91 </span><span>₱</span><span>78.27</span></div>
<div><span>Cleanfuel</span><span>83</span> stations</div>
<div><span>PTT</span><span>110</span> stations
  <span>Avg Diesel </span><span>₱</span><span>90.16</span>
  <span>Avg Unleaded 91 </span><span>₱</span><span>79.84</span></div>
</body></html>
"""


class ParsingTests(TestCase):
    def test_the_page_header_gives_scale_and_a_date(self):
        parsed = parse(PAGE)
        self.assertEqual(parsed.stations, 5626)
        self.assertEqual(parsed.cities, 153)
        self.assertEqual(parsed.updated_on, date(2026, 8, 18))

    def test_national_averages_are_read_for_every_grade(self):
        national = parse(PAGE).national
        self.assertEqual(national[FuelType.DIESEL], Decimal("90.52"))
        self.assertEqual(national[FuelType.GAS_97], Decimal("88.60"))
        self.assertEqual(len(national), 5)

    def test_brand_averages_are_read(self):
        brands = {b.brand: b for b in parse(PAGE).brands}
        self.assertEqual(brands["Petron"].prices[FuelType.DIESEL], Decimal("89.92"))
        self.assertEqual(brands["Petron"].stations, 1017)

    def test_a_brand_with_no_published_price_gets_none(self):
        # The whole reason this parser anchors on the next brand rather than
        # on the next number.
        brands = {b.brand: b for b in parse(PAGE).brands}

        self.assertEqual(brands["Cleanfuel"].stations, 83)
        self.assertEqual(brands["Cleanfuel"].prices, {})
        # And the brand after it keeps its own numbers.
        self.assertEqual(brands["PTT"].prices[FuelType.DIESEL], Decimal("90.16"))

    def test_a_page_with_no_brand_section_parses_without_inventing_one(self):
        parsed = parse("<html><body><p>nothing here</p></body></html>")
        self.assertEqual(parsed.brands, [])
        self.assertEqual(parsed.national, {})

    def test_markup_is_flattened_predictably(self):
        self.assertIn("Petron", to_text(PAGE))


class ImportCommandTests(TestCase):
    def _run(self, **options):
        out = StringIO()
        response = mock.Mock()
        response.text = PAGE
        response.raise_for_status.return_value = None

        with mock.patch(
            "apps.fuel.management.commands.import_metrofuel.httpx.get",
            return_value=response,
        ):
            call_command("import_metrofuel", stdout=out, stderr=StringIO(), **options)
        return out.getvalue()

    def test_brand_rows_are_written_for_the_current_week(self):
        self._run()

        rows = DOEAdvisory.objects.filter(week_of=week_start())
        self.assertEqual(rows.filter(brand="Petron").count(), 2)
        self.assertEqual(
            rows.get(brand="Petron", fuel_type=FuelType.DIESEL).price,
            Decimal("89.920"),
        )

    def test_a_brand_with_no_price_writes_nothing_and_says_so(self):
        output = self._run()

        self.assertFalse(DOEAdvisory.objects.filter(brand="Cleanfuel").exists())
        self.assertIn("no prices published", output)

    def test_brands_are_normalised_to_the_apps_spelling(self):
        # So the rows join to places imported from OSM.
        self._run()
        self.assertTrue(DOEAdvisory.objects.filter(brand="Petron").exists())

    def test_national_rows_are_off_by_default(self):
        # A regional survey is a better prevailing figure than a national one,
        # so this must not quietly overwrite it.
        self._run()
        self.assertFalse(
            DOEAdvisory.objects.filter(brand="", week_of=week_start()).exists()
        )

    def test_national_rows_can_be_asked_for(self):
        self._run(national=True)
        self.assertTrue(DOEAdvisory.objects.filter(brand="").exists())

    def test_the_sample_size_is_recorded(self):
        self._run()
        row = DOEAdvisory.objects.get(brand="Petron", fuel_type=FuelType.DIESEL)
        self.assertEqual(row.sample_size, 1017)
        self.assertIn("MetroFuel", row.source_note)

    def test_a_dry_run_writes_nothing(self):
        output = self._run(dry_run=True)
        self.assertEqual(DOEAdvisory.objects.count(), 0)
        self.assertIn("Dry run", output)

    def test_re_running_updates_rather_than_duplicating(self):
        self._run()
        self._run()
        self.assertEqual(
            DOEAdvisory.objects.filter(brand="Petron").count(), 2
        )

    def test_a_changed_page_structure_stops_rather_than_writing_nothing_quietly(self):
        response = mock.Mock()
        response.text = "<html><body>redesigned</body></html>"
        response.raise_for_status.return_value = None

        with mock.patch(
            "apps.fuel.management.commands.import_metrofuel.httpx.get",
            return_value=response,
        ):
            with self.assertRaises(CommandError):
                call_command("import_metrofuel", stdout=StringIO(), stderr=StringIO())
