"""Tests for the shared place model, the importer and the unified map."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from apps.fuel.models import PriceObservation

from .brands import normalise_brand
from .models import Place, PlaceKind
from .overpass import KIND_SELECTORS, build_query, resolve_areas


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

    def test_nodes_and_polygons_are_both_requested(self):
        query = build_query(resolve_areas(["NCR"])[0], "supermarket")
        self.assertIn("node", query)
        self.assertIn("way", query)
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


class ImportTests(TestCase):
    ELEMENTS = [
        {"type": "node", "id": 1, "lat": 14.58, "lon": 121.06,
         "tags": {"name": "Puregold Pasig", "brand": "Puregold Price Club",
                  "addr:city": "Pasig"}},
        {"type": "way", "id": 2, "center": {"lat": 14.59, "lon": 121.07},
         "tags": {"brand": "7 Eleven"}},
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

        self.assertEqual(Place.objects.filter(kind=PlaceKind.SUPERMARKET).count(), 2)
        self.assertEqual(Place.objects.get(osm_id=1).brand, "Puregold")
        self.assertEqual(Place.objects.get(osm_id=2).brand, "7-Eleven")

    def test_importing_two_kinds_keeps_them_apart(self):
        self._run(area=["NCR"], kind=["supermarket", "convenience"])

        self.assertEqual(Place.objects.count(), 4)
        self.assertEqual(Place.objects.filter(kind=PlaceKind.CONVENIENCE).count(), 2)

    def test_an_unknown_kind_stops_the_command(self):
        with self.assertRaises(CommandError):
            self._run(area=["NCR"], kind=["casino"])

    def test_reimporting_updates_in_place(self):
        self._run(area=["NCR"], kind=["supermarket"])
        self._run(area=["NCR"], kind=["supermarket"])
        self.assertEqual(Place.objects.count(), 2)


class MapEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)
        self.fuel = make_place(kind=PlaceKind.FUEL, osm_id=1, name="Fuel here")
        self.shop = make_place(kind=PlaceKind.SUPERMARKET, osm_id=2,
                               name="Shop here", brand="Puregold")
        self.url = reverse("places:places_json")
        self.box = {"south": 14.5, "west": 121.0, "north": 14.7, "east": 121.2}

    def test_every_kind_comes_back_when_none_is_filtered(self):
        payload = self.client.get(self.url, self.box).json()
        kinds = {p["kind"] for p in payload["places"]}
        self.assertEqual(kinds, {"fuel", "supermarket"})

    def test_filtering_by_kind_narrows_the_layer(self):
        payload = self.client.get(
            self.url, {**self.box, "kind": "supermarket"}
        ).json()
        self.assertEqual([p["kind"] for p in payload["places"]], ["supermarket"])

    def test_only_fuel_carries_a_price(self):
        PriceObservation.objects.create(
            place=self.fuel, fuel_type="gas_95", price=Decimal("76.10")
        )
        payload = self.client.get(self.url, {**self.box, "fuel": "gas_95"}).json()
        by_kind = {p["kind"]: p for p in payload["places"]}

        self.assertEqual(by_kind["fuel"]["price"], "76.100")
        # Inventing a figure for a supermarket would be exactly the dishonesty
        # the rest of the app avoids.
        self.assertIsNone(by_kind["supermarket"]["price"])

    def test_a_place_outside_the_box_is_not_returned(self):
        make_place(kind=PlaceKind.MARKET, osm_id=3, name="Cebu",
                   latitude=Decimal("10.300000"), longitude=Decimal("123.900000"))
        payload = self.client.get(self.url, self.box).json()
        self.assertNotIn("Cebu", " ".join(p["name"] for p in payload["places"]))

    def test_a_missing_box_is_a_bad_request(self):
        self.assertEqual(self.client.get(self.url).status_code, 400)

    def test_the_cap_is_declared(self):
        with self.settings(MAP_MAX_STATIONS=1):
            payload = self.client.get(self.url, self.box).json()
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["shown"], 1)

    def test_signed_out_callers_are_redirected(self):
        self.client.logout()
        response = self.client.get(self.url, self.box)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])


class PlaceScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_the_map_renders_empty_and_populated(self):
        self.assertEqual(self.client.get(reverse("places:map")).status_code, 200)
        make_place(kind=PlaceKind.MARKET, name="Agora")
        self.assertEqual(self.client.get(reverse("places:map")).status_code, 200)

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


class NearestTests(TestCase):
    """Ranking by distance, which is what a shared location is for."""

    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)
        self.url = reverse("places:places_json")
        self.box = {"south": 14.4, "west": 120.9, "north": 14.8, "east": 121.3}

        # Ortigas, then progressively further out.
        # Blank brands so display_name is the bare name and the assertions
        # below read as what they are testing.
        self.near = make_place(kind=PlaceKind.SUPERMARKET, osm_id=1, name="Near",
                               brand="", latitude=Decimal("14.586000"),
                               longitude=Decimal("121.061000"))
        self.mid = make_place(kind=PlaceKind.SUPERMARKET, osm_id=2, name="Alpha mid",
                              brand="", latitude=Decimal("14.620000"),
                              longitude=Decimal("121.061000"))
        self.far = make_place(kind=PlaceKind.SUPERMARKET, osm_id=3, name="Aaa far",
                              brand="", latitude=Decimal("14.660000"),
                              longitude=Decimal("121.061000"))

    def _nearest(self, **extra):
        return self.client.get(
            self.url, {**self.box, "lat": 14.5866, "lng": 121.0614, **extra}
        ).json()

    def test_results_come_back_nearest_first(self):
        payload = self._nearest()

        self.assertEqual(payload["sorted_by"], "distance")
        self.assertEqual(
            [p["name"] for p in payload["places"]], ["Near", "Alpha mid", "Aaa far"]
        )

    def test_alphabetical_order_would_have_given_the_wrong_answer(self):
        # The names are deliberately chosen so A-Z inverts the distance order;
        # without this the test would pass on a sort that never ran.
        payload = self.client.get(self.url, self.box).json()
        self.assertEqual(payload["sorted_by"], "name")
        self.assertEqual(payload["places"][0]["name"], "Aaa far")

    def test_each_place_reports_how_far_it_is(self):
        payload = self._nearest()
        by_name = {p["name"]: p for p in payload["places"]}

        self.assertIsNotNone(by_name["Near"]["distance_km"])
        self.assertLess(
            Decimal(by_name["Near"]["distance_km"]),
            Decimal(by_name["Aaa far"]["distance_km"]),
        )

    def test_no_distance_is_reported_without_a_location(self):
        payload = self.client.get(self.url, self.box).json()
        self.assertTrue(all(p["distance_km"] is None for p in payload["places"]))

    def test_a_pinned_place_still_leads_even_if_further(self):
        self.far.is_favorite = True
        self.far.save(update_fields=["is_favorite"])

        payload = self._nearest()
        self.assertEqual(payload["places"][0]["name"], "Aaa far")

    def test_the_nearest_survives_the_cap(self):
        # The bug this guards: cap first, sort second. That ranks an arbitrary
        # alphabetical slice and can drop the closest place entirely.
        with self.settings(MAP_MAX_STATIONS=1):
            payload = self._nearest()
        self.assertEqual([p["name"] for p in payload["places"]], ["Near"])

    def test_somewhere_beyond_the_search_radius_is_left_out(self):
        make_place(kind=PlaceKind.SUPERMARKET, osm_id=9, name="Cavite",
                   latitude=Decimal("14.420000"), longitude=Decimal("120.950000"))
        names = [p["name"] for p in self._nearest()["places"]]
        self.assertNotIn("Cavite", names)

    def test_distance_ranking_respects_the_kind_filter(self):
        make_place(kind=PlaceKind.FUEL, osm_id=10, name="Closest fuel",
                   latitude=Decimal("14.586500"), longitude=Decimal("121.061200"))
        payload = self._nearest(kind="supermarket")
        self.assertEqual(
            {p["kind"] for p in payload["places"]}, {"supermarket"}
        )
