"""
Tests for location calibration.

The point of the feature is that nothing fails silently when you travel. So the
tests are mostly about the app admitting what does not apply, rather than about
it working when everything does.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.fuel.models import DOEAdvisory
from apps.fuel.services import week_start

from .calibration import calibrate, coverage_by_region
from .locate import Fix, GeoCache, reverse_geocode
from .models import Place, PlaceKind

ORTIGAS = (14.5866, 121.0614)
BATANGAS = (13.7565, 121.0583)


def nominatim(region="Calabarzon", state="Batangas", city="Batangas City"):
    """A response shaped like the ones Nominatim actually returns for PH."""
    return {
        "address": {
            "city": city, "state": state, "region": region,
            "country_code": "ph",
        }
    }


def fake_get(payload, status=200):
    response = mock.Mock()
    response.status_code = status
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return mock.patch("apps.places.locate.httpx.get", return_value=response)


def make_place(osm_id, region, kind=PlaceKind.FUEL) -> Place:
    return Place.objects.create(
        kind=kind, osm_type=Place.OSMType.NODE, osm_id=osm_id,
        name="Somewhere", brand="Brand", region=region,
        latitude=Decimal("14.58"), longitude=Decimal("121.06"),
    )


class ReverseGeocodeTests(TestCase):
    def test_a_region_name_maps_to_the_doe_code(self):
        with fake_get(nominatim()):
            fix = reverse_geocode(*BATANGAS)

        self.assertTrue(fix.resolved)
        self.assertEqual(fix.region, "IV-A")
        self.assertEqual(fix.province, "Batangas")

    def test_metro_manila_maps_to_ncr(self):
        with fake_get(nominatim(region="Metro Manila", state="", city="Pasig")):
            fix = reverse_geocode(*ORTIGAS)
        self.assertEqual(fix.region, "NCR")

    def test_a_province_with_no_region_still_resolves(self):
        # Nominatim sometimes returns only the province; the app already maps
        # every province to its region, so that is enough.
        with fake_get(nominatim(region="", state="Cebu", city="Cebu City")):
            fix = reverse_geocode(10.3, 123.9)
        self.assertEqual(fix.region, "VII")

    def test_a_location_outside_the_country_is_refused(self):
        with fake_get({"address": {"country_code": "sg"}}):
            fix = reverse_geocode(1.35, 103.8)

        self.assertFalse(fix.resolved)
        self.assertIn("outside the Philippines", fix.error)

    def test_a_failed_lookup_degrades_rather_than_raises(self):
        # This screen gets opened in a car park on one bar of signal.
        import httpx

        with mock.patch("apps.places.locate.httpx.get",
                        side_effect=httpx.ConnectError("no network")):
            fix = reverse_geocode(*BATANGAS)

        self.assertFalse(fix.resolved)
        self.assertTrue(fix.error)

    def test_the_answer_is_cached_so_a_day_out_costs_one_lookup(self):
        with fake_get(nominatim()) as patched:
            reverse_geocode(*BATANGAS)
            reverse_geocode(BATANGAS[0] + 0.001, BATANGAS[1] + 0.001)

        self.assertEqual(patched.call_count, 1)
        self.assertEqual(GeoCache.objects.count(), 1)

    def test_a_far_enough_move_is_a_new_lookup(self):
        with fake_get(nominatim()) as patched:
            reverse_geocode(*BATANGAS)
            reverse_geocode(*ORTIGAS)
        self.assertEqual(patched.call_count, 2)


class CalibrationTests(TestCase):
    def test_a_region_with_no_places_is_reported(self):
        with fake_get(nominatim()):
            state = calibrate(*BATANGAS)

        keys = {gap.key for gap in state.gaps}
        self.assertIn("places", keys)
        # The fix has to be actionable, not a shrug.
        gap = next(g for g in state.gaps if g.key == "places")
        self.assertIn("import_places", gap.fix)
        self.assertIn("Batangas", gap.fix)

    def test_the_fuel_advisory_is_checked_per_region(self):
        make_place(1, "IV-A")
        make_place(2, "IV-A", kind=PlaceKind.SUPERMARKET)
        make_place(3, "IV-A", kind=PlaceKind.MARKET)
        # An advisory for Metro Manila says nothing about pumps in Batangas.
        DOEAdvisory.objects.create(
            week_of=week_start(), region="NCR", brand="",
            fuel_type="gas_95", price=Decimal("77.20"),
        )

        with fake_get(nominatim()):
            state = calibrate(*BATANGAS)

        self.assertIn("fuel", {gap.key for gap in state.gaps})

    def test_an_advisory_for_this_region_clears_that_gap(self):
        make_place(1, "IV-A")
        make_place(2, "IV-A", kind=PlaceKind.SUPERMARKET)
        make_place(3, "IV-A", kind=PlaceKind.MARKET)
        DOEAdvisory.objects.create(
            week_of=week_start(), region="IV-A", brand="",
            fuel_type="gas_95", price=Decimal("78.00"),
        )

        with fake_get(nominatim()):
            state = calibrate(*BATANGAS)

        self.assertNotIn("fuel", {gap.key for gap in state.gaps})

    def test_the_grocery_index_is_flagged_as_ncr_only_outside_ncr(self):
        with fake_get(nominatim()):
            state = calibrate(*BATANGAS)

        gap = next(g for g in state.gaps if g.key == "grocery")
        self.assertIn("NCR only", gap.detail)

    def test_inside_ncr_the_grocery_index_is_not_flagged(self):
        for index in range(3):
            make_place(index, "NCR", kind=list(PlaceKind)[index])
        DOEAdvisory.objects.create(
            week_of=week_start(), region="NCR", brand="",
            fuel_type="gas_95", price=Decimal("77.20"),
        )

        with fake_get(nominatim(region="Metro Manila", state="", city="Pasig")):
            state = calibrate(*ORTIGAS)

        self.assertNotIn("grocery", {gap.key for gap in state.gaps})
        self.assertTrue(state.is_calibrated)

    def test_a_thin_import_is_noted_without_crying_wolf(self):
        make_place(1, "IV-A")

        with fake_get(nominatim()):
            state = calibrate(*BATANGAS)

        gap = next(g for g in state.gaps if g.key == "place_kinds")
        self.assertEqual(gap.severity, "info")

    def test_an_unresolved_location_produces_no_false_gaps(self):
        import httpx

        with mock.patch("apps.places.locate.httpx.get",
                        side_effect=httpx.ConnectError("no network")):
            state = calibrate(*BATANGAS)

        self.assertFalse(state.resolved)
        self.assertEqual(state.gaps, [])


class CoverageTests(TestCase):
    def test_every_region_is_listed_even_with_nothing_in_it(self):
        make_place(1, "NCR")
        rows = {r["code"]: r for r in coverage_by_region()}

        self.assertEqual(rows["NCR"]["places"], 1)
        self.assertEqual(rows["VII"]["places"], 0)
        self.assertEqual(len(rows), 17)


class CalibrationScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_without_a_location_it_explains_the_problem(self):
        response = self.client.get(reverse("places:calibration"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Travelling?")
        self.assertIsNone(response.context["state"])

    def test_with_a_location_it_lists_what_does_not_apply(self):
        with fake_get(nominatim()):
            response = self.client.get(reverse("places:calibration"),
                                       {"lat": BATANGAS[0], "lng": BATANGAS[1]})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Batangas")
        self.assertGreater(len(response.context["state"].gaps), 0)

    def test_a_nonsense_location_falls_back_to_asking(self):
        response = self.client.get(reverse("places:calibration"),
                                   {"lat": "abc", "lng": "def"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["state"])

    def test_signed_out_users_read_the_screen(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("places:calibration")).status_code, 200
        )
