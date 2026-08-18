"""
Tests for the fuel module.

Weighted towards the parts that decide a number: which price wins, what a tank
really costs once the detour is paid for, and whether a fill-up can be logged
from any two of the three figures on a receipt.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.places.brands import normalise_brand
from .forms import FillUpForm
from apps.places.models import Place, PlaceKind

from .models import DOEAdvisory, FillUp, PriceObservation, PriceTier, Vehicle
from apps.places.regions import region_for
from .services import (
    fuel_economy,
    haversine_km,
    quotes_for,
    score_options,
    week_start,
)


def make_station(**overrides) -> Place:
    """A fuel place. Named for what it is in this module's language."""
    values = {
        "kind": PlaceKind.FUEL,
        "osm_type": Place.OSMType.NODE,
        "osm_id": overrides.pop("osm_id", Place.objects.count() + 1),
        "name": "Test Station",
        "brand": "Petron",
        "latitude": Decimal("14.580000"),
        "longitude": Decimal("121.060000"),
        "city": "Pasig",
        "region": "NCR",
    }
    values.update(overrides)
    return Place.objects.create(**values)


class BrandNormalisationTests(TestCase):
    def test_collapses_spelling_variants_to_one_brand(self):
        for raw in ("SEAOIL", "Seaoil", "Sea Oil", "SEAOIL Philippines"):
            self.assertEqual(normalise_brand(raw), "Seaoil", msg=raw)

    def test_maps_owner_to_the_name_on_the_forecourt(self):
        self.assertEqual(normalise_brand("Chevron"), "Caltex")
        self.assertEqual(normalise_brand("Pilipinas Shell"), "Shell")

    def test_keeps_an_unknown_independent_rather_than_discarding_it(self):
        self.assertEqual(normalise_brand("Villanueva Fuels"), "Villanueva")

    def test_blank_stays_blank(self):
        self.assertEqual(normalise_brand(""), "")

    def test_brand_leading_a_longer_name_still_matches(self):
        self.assertEqual(normalise_brand("Petron Ortigas Avenue"), "Petron")


class RegionMappingTests(TestCase):
    def test_province_decides_the_region(self):
        self.assertEqual(region_for(province="Rizal"), "IV-A")
        self.assertEqual(region_for(province="Cebu"), "VII")

    def test_ncr_city_is_enough_when_the_province_tag_is_missing(self):
        # Only 13% of Philippine stations carry addr:province, so this is the
        # path most Metro Manila stations actually take.
        self.assertEqual(region_for(city="Makati City"), "NCR")
        self.assertEqual(region_for(city="Quezon City"), "NCR")

    def test_unknown_place_returns_blank_rather_than_a_guess(self):
        self.assertEqual(region_for(province="Atlantis"), "")
        self.assertEqual(region_for(), "")


class PriceResolutionTests(TestCase):
    def setUp(self):
        self.station = make_station()
        self.week = week_start()

    def test_a_fresh_logged_price_wins(self):
        PriceObservation.objects.create(
            place=self.station, fuel_type="gas_95", price=Decimal("70.00")
        )
        DOEAdvisory.objects.create(
            week_of=self.week, region="NCR", brand="Petron",
            fuel_type="gas_95", price=Decimal("77.00"),
        )

        quote = quotes_for([self.station], "gas_95")[self.station.pk]
        self.assertEqual(quote.tier, PriceTier.LOGGED)
        self.assertEqual(quote.price, Decimal("70.00"))

    def test_brand_advisory_is_used_when_nothing_was_logged(self):
        DOEAdvisory.objects.create(
            week_of=self.week, region="NCR", brand="Petron",
            fuel_type="gas_95", price=Decimal("77.00"),
        )
        quote = quotes_for([self.station], "gas_95")[self.station.pk]
        self.assertEqual(quote.tier, PriceTier.ADVISORY)
        self.assertEqual(quote.price, Decimal("77.00"))

    def test_regional_prevailing_is_only_an_estimate(self):
        DOEAdvisory.objects.create(
            week_of=self.week, region="NCR", brand="",
            fuel_type="gas_95", price=Decimal("76.50"),
        )
        quote = quotes_for([self.station], "gas_95")[self.station.pk]
        self.assertEqual(quote.tier, PriceTier.ESTIMATED)

    def test_current_regional_price_beats_a_stale_receipt(self):
        PriceObservation.objects.create(
            place=self.station, fuel_type="gas_95", price=Decimal("70.00"),
            observed_at=timezone.now() - timedelta(days=45),
        )
        DOEAdvisory.objects.create(
            week_of=self.week, region="NCR", brand="",
            fuel_type="gas_95", price=Decimal("76.50"),
        )

        quote = quotes_for([self.station], "gas_95")[self.station.pk]
        self.assertEqual(quote.price, Decimal("76.50"))
        self.assertEqual(quote.tier, PriceTier.ESTIMATED)

    def test_a_stale_receipt_is_still_better_than_nothing(self):
        PriceObservation.objects.create(
            place=self.station, fuel_type="gas_95", price=Decimal("70.00"),
            observed_at=timezone.now() - timedelta(days=45),
        )
        quote = quotes_for([self.station], "gas_95")[self.station.pk]
        self.assertEqual(quote.tier, PriceTier.ESTIMATED)
        self.assertEqual(quote.price, Decimal("70.00"))

    def test_no_data_is_reported_as_no_price_not_as_zero(self):
        quote = quotes_for([self.station], "gas_95")[self.station.pk]
        self.assertEqual(quote.tier, PriceTier.UNKNOWN)
        self.assertIsNone(quote.price)

    def test_the_advisory_for_another_grade_is_not_borrowed(self):
        DOEAdvisory.objects.create(
            week_of=self.week, region="NCR", brand="Petron",
            fuel_type="diesel", price=Decimal("85.00"),
        )
        quote = quotes_for([self.station], "gas_95")[self.station.pk]
        self.assertEqual(quote.tier, PriceTier.UNKNOWN)

    def test_resolution_stays_at_two_queries_however_many_stations(self):
        for index in range(25):
            make_station(osm_id=1000 + index, name=f"Station {index}")
        stations = list(Place.objects.all())

        # One query for observations, one for advisories. A per-station lookup
        # here would be a query per pin on the map.
        with self.assertNumQueries(2):
            quotes_for(stations, "gas_95")


class ScoringTests(TestCase):
    def setUp(self):
        self.near = make_station(osm_id=1, name="Near", latitude=Decimal("14.580000"),
                                 longitude=Decimal("121.060000"))
        self.far = make_station(osm_id=2, name="Far", latitude=Decimal("14.680000"),
                                longitude=Decimal("121.160000"))
        week = week_start()
        DOEAdvisory.objects.create(week_of=week, region="NCR", brand="Petron",
                                   fuel_type="gas_95", price=Decimal("78.00"))

    def test_distance_is_measured_from_the_origin(self):
        options = score_options(
            [self.near, self.far], "gas_95",
            liters=Decimal("40"), km_per_liter=Decimal("10"),
            origin=(14.58, 121.06),
        )
        by_name = {o.place.name: o for o in options}
        self.assertEqual(by_name["Near"].distance_km, Decimal("0.00"))
        self.assertGreater(by_name["Far"].distance_km, Decimal("10"))

    def test_a_cheaper_station_can_lose_once_the_detour_is_paid_for(self):
        # 2 pesos a litre cheaper on 40 litres is 80 pesos. Put it far enough
        # away and the fuel burned getting there eats the difference.
        PriceObservation.objects.create(
            place=self.far, fuel_type="gas_95", price=Decimal("76.00")
        )
        PriceObservation.objects.create(
            place=self.near, fuel_type="gas_95", price=Decimal("78.00")
        )

        options = score_options(
            [self.near, self.far], "gas_95",
            liters=Decimal("40"), km_per_liter=Decimal("4"),
            origin=(14.58, 121.06),
        )
        self.assertEqual(options[0].place.name, "Near")
        self.assertGreater(options[1].detour_cost, Decimal("80"))

    def test_the_cheaper_station_wins_when_the_detour_is_small(self):
        PriceObservation.objects.create(
            place=self.far, fuel_type="gas_95", price=Decimal("70.00")
        )
        PriceObservation.objects.create(
            place=self.near, fuel_type="gas_95", price=Decimal("78.00")
        )
        options = score_options(
            [self.near, self.far], "gas_95",
            liters=Decimal("40"), km_per_liter=Decimal("14"),
            origin=(14.58, 121.06),
        )
        self.assertEqual(options[0].place.name, "Far")

    def test_stations_with_no_price_sort_last_but_are_not_dropped(self):
        priceless = make_station(osm_id=3, name="Unknown", brand="Nobody", region="")
        options = score_options(
            [priceless, self.near], "gas_95",
            liters=Decimal("40"), km_per_liter=Decimal("10"),
        )
        self.assertEqual(len(options), 2)
        self.assertEqual(options[-1].place.name, "Unknown")
        self.assertIsNone(options[-1].effective_cost)

    def test_haversine_matches_a_known_distance(self):
        # Manila to Cebu is about 570 km great-circle.
        km = haversine_km(14.5995, 120.9842, 10.3157, 123.8854)
        self.assertAlmostEqual(km, 570, delta=15)


class FuelEconomyTests(TestCase):
    def setUp(self):
        self.vehicle = Vehicle.objects.create(name="Car", is_default=True)
        self.station = make_station()

    def _fill(self, odometer, liters, when, full=True):
        return FillUp.objects.create(
            vehicle=self.vehicle, place=self.station, fuel_type="gas_95",
            filled_at=when, liters=Decimal(liters),
            price_per_liter=Decimal("78.00"),
            total_cost=Decimal(liters) * Decimal("78.00"),
            odometer_km=odometer, is_full_tank=full,
        )

    def test_full_to_full_gives_kilometres_per_litre(self):
        now = timezone.now()
        self._fill(10000, "40", now - timedelta(days=20))
        self._fill(10400, "40", now - timedelta(days=10))
        self.assertEqual(fuel_economy(FillUp.objects.all()), Decimal("10.00"))

    def test_a_partial_fill_is_left_out_of_the_calculation(self):
        now = timezone.now()
        self._fill(10000, "40", now - timedelta(days=30))
        self._fill(10200, "20", now - timedelta(days=20), full=False)
        self._fill(10400, "40", now - timedelta(days=10))
        # Only intervals that begin and end on a full tank count, and neither
        # interval here does, so there is no honest figure to give.
        self.assertIsNone(fuel_economy(FillUp.objects.all()))

    def test_one_fill_up_is_not_enough(self):
        self._fill(10000, "40", timezone.now())
        self.assertIsNone(fuel_economy(FillUp.objects.all()))


class FillUpFormTests(TestCase):
    def setUp(self):
        self.vehicle = Vehicle.objects.create(name="Car", is_default=True)
        self.station = make_station()
        self.base = {
            "vehicle": self.vehicle.pk,
            "place": self.station.pk,
            "fuel_type": "gas_95",
            "filled_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "is_full_tank": "on",
        }

    def test_total_is_derived_from_litres_and_price(self):
        form = FillUpForm({**self.base, "liters": "40", "price_per_liter": "78.50"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["total_cost"], Decimal("3140.00"))

    def test_litres_are_derived_from_total_and_price(self):
        form = FillUpForm({**self.base, "total_cost": "3140", "price_per_liter": "78.50"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["liters"], Decimal("40.000"))

    def test_price_is_derived_from_total_and_litres(self):
        form = FillUpForm({**self.base, "total_cost": "3140", "liters": "40"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["price_per_liter"], Decimal("78.500"))

    def test_one_figure_alone_is_rejected(self):
        form = FillUpForm({**self.base, "liters": "40"})
        self.assertFalse(form.is_valid())

    def test_three_figures_that_disagree_are_rejected(self):
        form = FillUpForm({
            **self.base, "liters": "40", "price_per_liter": "78.50",
            "total_cost": "2000",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("total_cost", form.errors)

    def test_rounding_of_a_peso_or_less_is_tolerated(self):
        form = FillUpForm({
            **self.base, "liters": "40", "price_per_liter": "78.50",
            "total_cost": "3140.50",
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_saving_publishes_the_price_to_the_station(self):
        form = FillUpForm({**self.base, "liters": "40", "price_per_liter": "78.50"})
        self.assertTrue(form.is_valid(), form.errors)
        fill_up = form.save()

        observation = PriceObservation.objects.get(place=self.station)
        self.assertEqual(observation.price, Decimal("78.500"))
        self.assertEqual(observation.source, PriceObservation.Source.FILL_UP)
        self.assertEqual(fill_up.observation, observation)

        quote = quotes_for([self.station], "gas_95")[self.station.pk]
        self.assertEqual(quote.tier, PriceTier.LOGGED)

    def test_editing_moves_the_published_price_instead_of_adding_one(self):
        form = FillUpForm({**self.base, "liters": "40", "price_per_liter": "78.50"})
        self.assertTrue(form.is_valid(), form.errors)
        fill_up = form.save()

        edit = FillUpForm(
            {**self.base, "liters": "40", "price_per_liter": "80.00"},
            instance=fill_up,
        )
        self.assertTrue(edit.is_valid(), edit.errors)
        edit.save()

        self.assertEqual(PriceObservation.objects.count(), 1)
        self.assertEqual(
            PriceObservation.objects.get().price, Decimal("80.000")
        )


class StationMapEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)
        self.inside = make_station(osm_id=1, name="Inside",
                                   latitude=Decimal("14.580000"),
                                   longitude=Decimal("121.060000"))
        self.outside = make_station(osm_id=2, name="Outside",
                                    latitude=Decimal("10.300000"),
                                    longitude=Decimal("123.900000"))
        self.url = reverse("fuel:stations_json")

    def test_only_stations_in_the_box_are_returned(self):
        response = self.client.get(self.url, {
            "south": 14.5, "west": 121.0, "north": 14.7, "east": 121.2,
        })
        self.assertEqual(response.status_code, 200)
        # display_name prefixes the brand, so match on the substring.
        names = " | ".join(s["name"] for s in response.json()["stations"])
        self.assertIn("Inside", names)
        self.assertNotIn("Outside", names)

    def test_a_missing_bounding_box_is_a_bad_request(self):
        self.assertEqual(self.client.get(self.url).status_code, 400)

    def test_signed_out_callers_are_sent_to_the_login_page(self):
        self.client.logout()
        response = self.client.get(self.url, {
            "south": 14.5, "west": 121.0, "north": 14.7, "east": 121.2,
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_the_cap_is_declared_rather_than_hidden(self):
        # A box wide enough to hold both stations, so the cap has something to
        # actually cut.
        with self.settings(MAP_MAX_STATIONS=1):
            response = self.client.get(self.url, {
                "south": 10.0, "west": 120.0, "north": 15.0, "east": 124.0,
            })
        payload = response.json()
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["shown"], 1)

    def test_favourites_survive_the_cap(self):
        self.outside.latitude = Decimal("14.590000")
        self.outside.longitude = Decimal("121.070000")
        self.outside.is_favorite = True
        self.outside.save()

        with self.settings(MAP_MAX_STATIONS=1):
            response = self.client.get(self.url, {
                "south": 14.5, "west": 121.0, "north": 14.7, "east": 121.2,
            })
        self.assertIn("Outside", response.json()["stations"][0]["name"])


class StationFavouriteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)
        self.station = make_station()

    def test_an_offsite_next_target_is_refused(self):
        response = self.client.post(
            reverse("fuel:station_favorite", args=[self.station.pk]),
            {"next": "https://example.com/phish"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("example.com", response["Location"])

    def test_a_local_next_target_is_honoured(self):
        response = self.client.post(
            reverse("fuel:station_favorite", args=[self.station.pk]),
            {"next": "/fuel/stations/"},
        )
        self.assertEqual(response["Location"], "/fuel/stations/")


class ScreenSmokeTests(TestCase):
    """Every screen renders for a signed-in user with an empty database.

    Empty is the state the app is actually in on day one, and it is the state
    that finds the template that assumes a row exists.
    """

    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_screens_render_when_there_is_no_data(self):
        for name in ("core:home", "fuel:map", "fuel:stations", "fuel:fillups",
                     "fuel:advisory", "fuel:vehicles", "fuel:vehicle_create"):
            with self.subTest(screen=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    def test_screens_render_with_data(self):
        vehicle = Vehicle.objects.create(name="Car", is_default=True)
        station = make_station()
        fill_up = FillUp.objects.create(
            vehicle=vehicle, place=station, fuel_type="gas_95",
            liters=Decimal("40"), price_per_liter=Decimal("78.5"),
            total_cost=Decimal("3140"), odometer_km=10000,
        )
        fill_up.sync_observation()
        DOEAdvisory.objects.create(
            week_of=week_start(), region="NCR", brand="Petron",
            fuel_type="diesel", price=Decimal("85.00"),
        )

        for url in (
            reverse("core:home"),
            reverse("fuel:stations"),
            reverse("fuel:station_detail", args=[station.pk]),
            reverse("fuel:fillups"),
            reverse("fuel:fillup_edit", args=[fill_up.pk]),
            reverse("fuel:advisory"),
            reverse("fuel:vehicles"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_signed_out_users_reach_no_screen(self):
        self.client.logout()
        for name in ("core:home", "fuel:map", "fuel:stations", "fuel:fillups"):
            with self.subTest(screen=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login/", response["Location"])


class AdvisoryImportTests(TestCase):
    """The CSV path, which is how a downloaded DOE bulletin gets in."""

    def _write(self, body: str, suffix: str = ".csv") -> str:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=suffix, delete=False, encoding="utf-8", newline=""
        )
        handle.write(body)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_a_clean_file_loads_every_row(self):
        path = self._write(
            "region,brand,fuel_type,price\n"
            "NCR,,gas_95,77.20\n"
            "NCR,Petron,gas_95,78.45\n"
            "IV-A,,diesel,87.38\n"
        )
        call_command("import_doe_advisory", file=path, week_of="2026-08-17",
                     stdout=StringIO())

        self.assertEqual(DOEAdvisory.objects.count(), 3)
        # 17 Aug 2026 is a Monday, so the week key is that same date.
        self.assertEqual(
            DOEAdvisory.objects.filter(week_of=date(2026, 8, 17)).count(), 3
        )

    def test_loose_headings_and_product_names_are_understood(self):
        path = self._write(
            "Area,Company,Product,Retail Price\n"
            "NCR,Sea Oil,Unleaded 91,76.10\n"
            "NCR,Chevron,Premium Diesel,90.05\n"
        )
        call_command("import_doe_advisory", file=path, stdout=StringIO())

        self.assertTrue(
            DOEAdvisory.objects.filter(brand="Seaoil", fuel_type="gas_91").exists()
        )
        self.assertTrue(
            DOEAdvisory.objects.filter(
                brand="Caltex", fuel_type="diesel_premium"
            ).exists()
        )

    def test_prevailing_synonyms_all_mean_a_blank_brand(self):
        path = self._write(
            "region,brand,fuel_type,price\n"
            "NCR,Prevailing,gas_95,77.20\n"
        )
        call_command("import_doe_advisory", file=path, stdout=StringIO())
        self.assertEqual(DOEAdvisory.objects.get().brand, "")

    def test_a_peso_sign_and_thousands_separator_are_stripped(self):
        path = self._write(
            "region,brand,fuel_type,price\n"
            'NCR,,gas_95,"P1,077.20"\n'
        )
        call_command("import_doe_advisory", file=path, stdout=StringIO())
        self.assertEqual(DOEAdvisory.objects.get().price, Decimal("1077.20"))

    def test_bad_rows_are_reported_and_the_good_ones_still_load(self):
        path = self._write(
            "region,brand,fuel_type,price\n"
            "NCR,,gas_95,77.20\n"
            "ATLANTIS,,gas_95,70.00\n"
            "NCR,,rocket fuel,70.00\n"
            "NCR,,diesel,not-a-number\n"
        )
        errors = StringIO()
        call_command("import_doe_advisory", file=path, stdout=StringIO(), stderr=errors)

        self.assertEqual(DOEAdvisory.objects.count(), 1)
        reported = errors.getvalue()
        self.assertIn("unknown region", reported)
        self.assertIn("unrecognised fuel", reported)
        self.assertIn("bad price", reported)

    def test_reloading_the_same_week_corrects_rather_than_duplicates(self):
        first = self._write(
            "region,brand,fuel_type,price\nNCR,,gas_95,77.20\n"
        )
        corrected = self._write(
            "region,brand,fuel_type,price\nNCR,,gas_95,79.90\n"
        )
        call_command("import_doe_advisory", file=first, week_of="2026-08-17",
                     stdout=StringIO())
        call_command("import_doe_advisory", file=corrected, week_of="2026-08-17",
                     stdout=StringIO())

        self.assertEqual(DOEAdvisory.objects.count(), 1)
        self.assertEqual(DOEAdvisory.objects.get().price, Decimal("79.90"))

    def test_a_missing_file_stops_rather_than_loading_nothing_quietly(self):
        with self.assertRaises(CommandError):
            call_command("import_doe_advisory", file="no-such-file.csv",
                         stdout=StringIO())


class StationImportTests(TestCase):
    """Ingestion of Overpass elements, with the network stubbed out."""

    ELEMENTS = [
        {"type": "node", "id": 1, "lat": 14.58, "lon": 121.06,
         "tags": {"amenity": "fuel", "name": "Ortigas", "brand": "SEAOIL",
                  "addr:city": "Pasig", "addr:street": "Ortigas Ave"}},
        # A polygon: coordinates arrive as a computed centre, not lat/lon.
        {"type": "way", "id": 2, "center": {"lat": 14.59, "lon": 121.07},
         "tags": {"amenity": "fuel", "brand": "Clean Fuel"}},
        # No geometry at all - must be skipped, not written at 0,0.
        {"type": "way", "id": 3, "tags": {"amenity": "fuel", "brand": "Shell"}},
    ]

    def _run(self, **kwargs):
        out = StringIO()
        with mock.patch("apps.places.management.commands.import_places.fetch_area",
                        return_value=self.ELEMENTS):
            call_command("import_places", area=["NCR"], kind=["fuel"], stdout=out,
                         stderr=StringIO(), **kwargs)
        return out.getvalue()

    def test_nodes_and_polygons_both_land_and_brands_are_normalised(self):
        self._run()

        self.assertEqual(Place.objects.count(), 2)
        seaoil = Place.objects.get(osm_id=1)
        self.assertEqual(seaoil.brand, "Seaoil")
        self.assertEqual(seaoil.brand_raw, "SEAOIL")
        self.assertEqual(seaoil.city, "Pasig")
        self.assertEqual(Place.objects.get(osm_id=2).brand, "Cleanfuel")

    def test_region_comes_from_the_area_queried_not_the_tags(self):
        # Neither element carries addr:province, which is the normal case.
        self._run()
        self.assertEqual(
            set(Place.objects.values_list("region", flat=True)), {"NCR"}
        )

    def test_an_element_without_coordinates_is_skipped_and_counted(self):
        output = self._run()
        self.assertFalse(Place.objects.filter(osm_id=3).exists())
        self.assertIn("1 skipped", output)

    def test_reimporting_updates_in_place_instead_of_duplicating(self):
        self._run()
        self._run()
        self.assertEqual(Place.objects.count(), 2)

    def test_a_dry_run_writes_nothing(self):
        output = self._run(dry_run=True)
        self.assertEqual(Place.objects.count(), 0)
        self.assertIn("Dry run", output)

    def test_a_pinned_station_keeps_its_pin_across_a_reimport(self):
        self._run()
        station = Place.objects.get(osm_id=1)
        station.is_favorite = True
        station.save(update_fields=["is_favorite"])

        self._run()
        self.assertTrue(Place.objects.get(osm_id=1).is_favorite)


class PriceCoverageTests(TestCase):
    """The empty state has to explain itself, not just look broken."""

    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)
        self.station = make_station()

    def test_with_no_advisory_the_map_says_why_and_offers_the_fix(self):
        response = self.client.get(reverse("fuel:map"))

        self.assertContains(response, "Nothing is priced yet")
        # The fix is a link, not a paragraph telling you to go and find it.
        self.assertContains(response, reverse("fuel:advisory"))

    def test_the_stations_list_carries_the_same_prompt(self):
        response = self.client.get(reverse("fuel:stations"))
        self.assertContains(response, "Nothing is priced yet")

    def test_a_stale_advisory_reads_differently_from_none_at_all(self):
        DOEAdvisory.objects.create(
            week_of=week_start() - timedelta(days=21), region="NCR", brand="",
            fuel_type="gas_95", price=Decimal("77.20"),
        )
        response = self.client.get(reverse("fuel:map"))

        # Falling back to an old week is a different problem from having
        # nothing, and needs a different prompt.
        self.assertContains(response, "No advisory for the week")
        self.assertNotContains(response, "Nothing is priced yet")

    def test_once_this_week_is_entered_the_warning_goes(self):
        DOEAdvisory.objects.create(
            week_of=week_start(), region="NCR", brand="",
            fuel_type="gas_95", price=Decimal("77.20"),
        )
        response = self.client.get(reverse("fuel:map"))

        self.assertNotContains(response, "Nothing is priced yet")
        self.assertContains(response, "Priced")

    def test_one_prevailing_row_prices_every_station(self):
        # The claim the banner makes, pinned: a single blank-brand NCR row
        # gives every station in the region a price.
        for index in range(5):
            make_station(osm_id=500 + index, brand=f"Brand {index}")
        DOEAdvisory.objects.create(
            week_of=week_start(), region="NCR", brand="",
            fuel_type="gas_95", price=Decimal("77.20"),
        )

        stations = list(Place.objects.filter(kind=PlaceKind.FUEL))
        quotes = quotes_for(stations, "gas_95")
        self.assertTrue(all(q.price == Decimal("77.20") for q in quotes.values()))
        self.assertTrue(all(q.tier == PriceTier.ESTIMATED for q in quotes.values()))


class PriceBandTests(TestCase):
    """A market survey reports a range; the spread is the useful half."""

    def test_an_unbranded_station_gets_the_estimated_tier_not_advisory(self):
        # A station OSM never tagged has an empty brand. Matching it against
        # the blank-brand prevailing row at the advisory tier would dress a
        # regional median up as a brand-specific figure.
        unbranded = make_station(osm_id=900, brand="")
        DOEAdvisory.objects.create(
            week_of=week_start(), region="NCR", brand="",
            fuel_type="gas_95", price=Decimal("81.60"),
        )

        quote = quotes_for([unbranded], "gas_95")[unbranded.pk]
        self.assertEqual(quote.tier, PriceTier.ESTIMATED)

    def test_a_branded_station_still_prefers_its_brand_row(self):
        branded = make_station(osm_id=901, brand="Petron")
        DOEAdvisory.objects.create(
            week_of=week_start(), region="NCR", brand="",
            fuel_type="gas_95", price=Decimal("81.60"),
        )
        DOEAdvisory.objects.create(
            week_of=week_start(), region="NCR", brand="Petron",
            fuel_type="gas_95", price=Decimal("83.00"),
        )

        quote = quotes_for([branded], "gas_95")[branded.pk]
        self.assertEqual(quote.tier, PriceTier.ADVISORY)
        self.assertEqual(quote.price, Decimal("83.00"))

    def test_the_spread_is_reported_per_litre_and_per_tank(self):
        advisory = DOEAdvisory.objects.create(
            week_of=week_start(), region="NCR", brand="",
            fuel_type="gas_91", price=Decimal("79.00"),
            low=Decimal("67.80"), high=Decimal("90.77"), sample_size=1292,
        )
        # A per-litre gap is easy to shrug at; a tank of it is not.
        self.assertEqual(advisory.spread, Decimal("22.970"))
        self.assertEqual(advisory.spread_on_a_tank, Decimal("918.80"))

    def test_a_row_with_no_range_reports_no_spread(self):
        advisory = DOEAdvisory.objects.create(
            week_of=week_start(), region="NCR", brand="Petron",
            fuel_type="gas_95", price=Decimal("83.00"),
        )
        self.assertIsNone(advisory.spread)
        self.assertIsNone(advisory.spread_on_a_tank)
