"""Tests for the shell: navigation, the offline path and the service worker."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.fuel.models import DOEAdvisory, PriceObservation
from apps.places.models import Place, PlaceKind

from .categories import SpendCategory, category_for_place
from .navigation import MODULES, grouped_modules


class NavigationTests(TestCase):
    def test_every_module_points_at_a_url_that_resolves(self):
        from django.urls import NoReverseMatch, reverse as rev

        for module in MODULES:
            with self.subTest(module=module.code):
                try:
                    rev(module.url_name)
                except NoReverseMatch as exc:
                    self.fail(f"{module.code}: {exc}")

    def test_module_codes_are_unique(self):
        codes = [m.code for m in MODULES]
        self.assertEqual(len(codes), len(set(codes)))

    def test_grouping_follows_declaration_order(self):
        groups = grouped_modules()
        flattened = [m.code for g in groups for m in g["modules"]]
        self.assertEqual(flattened, [m.code for m in MODULES])


class CategoryTests(TestCase):
    def test_an_unknown_place_kind_falls_back_rather_than_raising(self):
        self.assertEqual(category_for_place("casino"), SpendCategory.OTHER)


class OfflineTests(TestCase):
    def test_the_offline_page_works_signed_out(self):
        # The worker caches it at install time, which can happen before anyone
        # has signed in.
        response = self.client.get(reverse("core:offline"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "offline")

    def test_the_offline_page_works_signed_in(self):
        User.objects.create_user("driver", password="not-a-real-password")
        self.client.login(username="driver", password="not-a-real-password")

        response = self.client.get(reverse("core:offline"))
        self.assertEqual(response.status_code, 200)

    def test_the_worker_is_served_from_the_site_root(self):
        # Scope matters: a worker served from /static/ could only control
        # /static/ and would never see a page navigation.
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")

    def test_the_worker_refuses_to_cache_authenticated_entry_points(self):
        body = self.client.get("/sw.js").content.decode()
        # A cached admin or login page served after sign-out would be a leak.
        self.assertIn("/admin", body)
        self.assertIn("/login", body)


class PublicAccessTests(TestCase):
    """Reading is for everyone; writing is for the account behind the data."""

    def setUp(self):
        self.station = Place.objects.create(
            osm_id=4242, kind=PlaceKind.FUEL, name="Petron Ortigas", brand="Petron",
            latitude=Decimal("14.58"), longitude=Decimal("121.06"), region="NCR",
        )

    def test_every_screen_reads_without_an_account(self):
        screens = [
            reverse("core:home"),
            reverse("insights:briefing"),
            reverse("fuel:map"),
            reverse("fuel:advisory"),
            reverse("fuel:station_detail", args=[self.station.pk]),
            reverse("grocery:map"),
            reverse("grocery:commodities"),
            reverse("spend:where"),
            reverse("spend:card_promos"),
            reverse("places:calibration"),
        ]
        for url in screens:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_map_data_endpoints_are_public_too(self):
        # The maps are useless if the screen loads and its markers do not.
        response = self.client.get(
            reverse("fuel:stations_json"),
            {"north": "15", "south": "14", "east": "122", "west": "120"},
        )
        self.assertEqual(response.status_code, 200)

    def test_writing_a_price_without_an_account_is_sent_to_the_login(self):
        response = self.client.post(
            reverse("fuel:station_detail", args=[self.station.pk]),
            {"fuel_type": "gas_95", "price": "1.00", "observed_at": "2026-01-01"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.assertEqual(PriceObservation.objects.count(), 0)

    def test_writing_an_advisory_without_an_account_is_sent_to_the_login(self):
        response = self.client.post(reverse("fuel:advisory"), {
            "week_of": "2026-01-06", "region": "NCR", "price_gas_95": "77.20",
        })

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.assertEqual(DOEAdvisory.objects.count(), 0)

    def test_pinning_without_an_account_changes_nothing(self):
        response = self.client.post(
            reverse("fuel:station_favorite", args=[self.station.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.station.refresh_from_db()
        self.assertFalse(self.station.is_favorite)
