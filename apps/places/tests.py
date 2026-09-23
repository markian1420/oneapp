"""Tests for the shared place model, importer and place detail screens."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from .brands import normalise_brand
from .models import Place, PlaceKind
from .overpass import KIND_SELECTORS, build_query, fetch_area, resolve_areas


def make_place(**overrides) -> Place:
    values = {
        "kind": PlaceKind.FUEL,
        "osm_type": Place.OSMType.NODE,
        "osm_id": overrides.pop("osm_id", Place.objects.count() + 1),
        "name": "Somewhere",
        "brand": "Petron",
        "latitude": Decimal("14.580000"),
        "longitude": Decimal("121.060000"),
        "city": "Pasig",
        "region": "NCR",
    }
    values.update(overrides)
    return Place.objects.create(**values)


class RetailBrandTests(TestCase):
    def test_convenience_and_supermarket_variants_collapse(self):
        for raw in ("7-Eleven", "7 Eleven", "SEVEN ELEVEN", "711"):
            self.assertEqual(normalise_brand(raw), "7-Eleven", msg=raw)
        self.assertEqual(normalise_brand("Puregold Price Club"), "Puregold")
        self.assertEqual(normalise_brand("SM Savemore"), "Savemore")

    def test_fuel_brands_still_normalise_after_the_move(self):
        self.assertEqual(normalise_brand("Sea Oil"), "Seaoil")
        self.assertEqual(normalise_brand("Chevron"), "Caltex")


class QueryBuildingTests(TestCase):
    def test_each_kind_has_a_selector(self):
        # A kind the map offers but the importer cannot fetch would render an
        # empty layer with no explanation.
        for kind in PlaceKind:
            self.assertIn(kind.value, KIND_SELECTORS, msg=kind.value)

    def test_a_kind_with_two_selectors_queries_both(self):
        area = resolve_areas(["NCR"])[0]
        query = build_query(area, "mall")

        # OSM separates a mall from a department store; for deciding where to
        # shop that is a distinction without a difference.
        self.assertIn('"shop"="mall"', query)
        self.assertIn('"shop"="department_store"', query)

    def test_every_element_type_is_requested(self):
        # OSM maps the same station three ways depending on who mapped it, and
        # a query that asks for two of them reports no error for the third -
        # it just returns fewer places than exist.
        query = build_query(resolve_areas(["NCR"])[0], "supermarket")
        for element in ("node", "way", "relation"):
            with self.subTest(element=element):
                self.assertIn(f"  {element}[", query)
        self.assertIn("out tags center", query)

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            build_query(resolve_areas(["NCR"])[0], "casino")


class PlaceIdentityTests(TestCase):
    def test_the_same_osm_element_can_be_two_kinds(self):
        # A fuel station with a shop on the forecourt is tagged as both, and
        # both pins are legitimate.
        make_place(kind=PlaceKind.FUEL, osm_id=42)
        make_place(kind=PlaceKind.CONVENIENCE, osm_id=42, brand="7-Eleven")
        self.assertEqual(Place.objects.filter(osm_id=42).count(), 2)

    def test_display_name_combines_brand_and_name_without_repeating(self):
        self.assertEqual(
            make_place(brand="Petron", name="Ortigas").display_name, "Petron - Ortigas"
        )
        self.assertEqual(
            make_place(osm_id=2, brand="Petron", name="Petron Ortigas").display_name,
            "Petron Ortigas",
        )

    def test_an_unnamed_place_still_reads_as_something(self):
        place = make_place(osm_id=3, name="", brand="", kind=PlaceKind.MARKET)
        self.assertIn("Public market", place.display_name)

    def test_every_kind_has_a_map_style(self):
        for kind in PlaceKind:
            style = make_place(osm_id=100 + list(PlaceKind).index(kind), kind=kind).style
            self.assertIn("tone", style)
            self.assertIn("icon", style)


class FetchTests(TestCase):
    """What comes back from a public Overpass instance, and what to believe."""

    class _Response:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

    def test_an_empty_answer_is_checked_against_another_instance(self):
        # A national mirror answers a Philippine query with a valid, empty,
        # wrong result - 200, parseable, and nothing in it. Believing the
        # first one would quietly import no stations and report success.
        responses = [
            self._Response({"elements": []}),
            self._Response({"elements": [{"type": "node", "id": 7}]}),
        ]
        with mock.patch("apps.places.overpass.httpx.post",
                        side_effect=responses) as posted:
            elements = fetch_area(resolve_areas(["NCR"])[0], "fuel")

        self.assertEqual(len(elements), 1)
        self.assertEqual(posted.call_count, 2)
        # And against a different instance, not the one that just said nothing.
        first, second = (call.args[0] for call in posted.call_args_list)
        self.assertNotEqual(first, second)

    def test_an_empty_answer_confirmed_once_is_accepted(self):
        # Somewhere genuinely has no pharmacies. Confirming it twice is enough;
        # paying the whole retry schedule to hear it again is not.
        responses = [self._Response({"elements": []}) for _ in range(5)]
        with mock.patch("apps.places.overpass.httpx.post",
                        side_effect=responses) as posted:
            elements = fetch_area(resolve_areas(["NCR"])[0], "pharmacy")

        self.assertEqual(elements, [])
        self.assertEqual(posted.call_count, 2)


class ImportTests(TestCase):
    ELEMENTS = [
        {"type": "node", "id": 1, "lat": 14.58, "lon": 121.06,
         "tags": {"name": "Puregold Pasig", "brand": "Puregold Price Club",
                  "addr:city": "Pasig"}},
        {"type": "way", "id": 2, "center": {"lat": 14.59, "lon": 121.07},
         "tags": {"brand": "7 Eleven"}},
        # A forecourt mapped as a multipolygon. Real, and previously dropped:
        # the query asked only for nodes and ways.
        {"type": "relation", "id": 3, "center": {"lat": 14.60, "lon": 121.08},
         "tags": {"name": "Petron Capitol Commons", "brand": "Petron"}},
    ]

    def _run(self, **kwargs):
        out = StringIO()
        with mock.patch(
            "apps.places.management.commands.import_places.fetch_area",
            return_value=self.ELEMENTS,
        ):
            call_command("import_places", stdout=out, stderr=StringIO(), **kwargs)
        return out.getvalue()

    def test_kind_is_recorded_and_brands_normalised(self):
        self._run(area=["NCR"], kind=["supermarket"])

        self.assertEqual(Place.objects.filter(kind=PlaceKind.SUPERMARKET).count(), 3)
        self.assertEqual(Place.objects.get(osm_id=1).brand, "Puregold")
        self.assertEqual(Place.objects.get(osm_id=2).brand, "7-Eleven")

    def test_a_place_mapped_as_a_relation_is_imported_from_its_centre(self):
        self._run(area=["NCR"], kind=["fuel"])

        station = Place.objects.get(osm_id=3)
        self.assertEqual(station.osm_type, "relation")
        self.assertEqual(station.brand, "Petron")
        self.assertEqual(float(station.latitude), 14.60)

    def test_importing_two_kinds_keeps_them_apart(self):
        self._run(area=["NCR"], kind=["supermarket", "convenience"])

        self.assertEqual(Place.objects.count(), 6)
        self.assertEqual(Place.objects.filter(kind=PlaceKind.CONVENIENCE).count(), 3)

    def test_an_unknown_kind_stops_the_command(self):
        with self.assertRaises(CommandError):
            self._run(area=["NCR"], kind=["casino"])

    def test_reimporting_updates_in_place(self):
        self._run(area=["NCR"], kind=["supermarket"])
        self._run(area=["NCR"], kind=["supermarket"])
        self.assertEqual(Place.objects.count(), 3)


class PlaceScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_a_non_fuel_place_has_its_own_screen(self):
        market = make_place(kind=PlaceKind.MARKET, name="Agora Public Market")
        response = self.client.get(reverse("places:detail", args=[market.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Agora Public Market")

    def test_a_fuel_place_redirects_to_the_richer_fuel_screen(self):
        fuel = make_place(kind=PlaceKind.FUEL)
        response = self.client.get(reverse("places:detail", args=[fuel.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/fuel/stations/", response["Location"])

    def test_pinning_refuses_an_offsite_return(self):
        place = make_place(kind=PlaceKind.MARKET)
        response = self.client.post(
            reverse("places:favorite", args=[place.pk]),
            {"next": "https://example.com/phish"},
        )
        self.assertNotIn("example.com", response["Location"])
        place.refresh_from_db()
        self.assertTrue(place.is_favorite)
