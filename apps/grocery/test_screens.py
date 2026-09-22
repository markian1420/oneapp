"""
Tests for the movement maths, the chart geometry and the grocery screens.

Kept apart from tests.py, which covers the DA PDF parser: that file is about
reading somebody else's document, this one is about what we do with the numbers
once we have them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.categories import SpendCategory
from apps.places.models import Place, PlaceKind
from apps.spend.models import Purchase, PurchaseItem

from .chart import build as build_chart
from .models import Commodity, CommodityCategory, CommodityPrice
from .services import biggest_movers, movements


def make_series(commodity, points, *, region="NCR"):
    for observed_on, price in points:
        CommodityPrice.objects.create(
            commodity=commodity, region=region,
            observed_on=observed_on, price=Decimal(price),
        )


def make_store(**overrides) -> Place:
    values = {
        "kind": PlaceKind.SUPERMARKET,
        "osm_type": Place.OSMType.NODE,
        "osm_id": 9000,
        "name": "Puregold",
        "brand": "Puregold",
        "latitude": Decimal("14.580000"),
        "longitude": Decimal("121.060000"),
        "region": "NCR",
    }
    values.update(overrides)
    return Place.objects.create(**values)


def log_item(store: Place, description: str, unit_price: str) -> None:
    purchase = Purchase.objects.create(
        category=SpendCategory.GROCERY,
        place=store,
        total=Decimal(unit_price),
    )
    PurchaseItem.objects.create(
        purchase=purchase,
        description=description,
        amount=Decimal(unit_price),
        unit_price=Decimal(unit_price),
    )


class MovementTests(TestCase):
    def setUp(self):
        self.pechay = Commodity.objects.create(
            category=CommodityCategory.LOWLAND_VEG, name="Native Pechay", unit="kg"
        )

    def test_change_is_measured_against_the_window_not_the_last_reading(self):
        make_series(self.pechay, [
            (date(2026, 8, 3), "100.00"),
            (date(2026, 8, 10), "150.00"),
            (date(2026, 8, 11), "155.00"),
        ])
        movement = movements([self.pechay])[self.pechay.pk]

        self.assertEqual(movement.latest, Decimal("155.00"))
        self.assertEqual(movement.previous, Decimal("150.00"))
        # Seven days before 11 Aug is 4 Aug; the nearest reading on or before
        # that is 3 Aug at 100, so the week's move is +55, not the +5 you get
        # from comparing with yesterday.
        self.assertEqual(movement.week_change, Decimal("55.00"))
        self.assertEqual(movement.week_pct, Decimal("55.0"))

    def test_a_short_series_does_not_claim_to_know_a_month(self):
        make_series(self.pechay, [
            (date(2026, 8, 10), "100.00"),
            (date(2026, 8, 11), "110.00"),
        ])
        movement = movements([self.pechay])[self.pechay.pk]

        self.assertIsNone(movement.month_change)
        self.assertIsNone(movement.month_pct)

    def test_a_rise_reads_as_bad_news_for_a_shopper(self):
        make_series(self.pechay, [
            (date(2026, 8, 3), "100.00"), (date(2026, 8, 11), "150.00"),
        ])
        movement = movements([self.pechay])[self.pechay.pk]

        # Inverted against the finance convention on purpose: for someone
        # buying, a price going up is the red one.
        self.assertEqual(movement.direction, "up")
        self.assertEqual(movement.badge_class, "badge-danger")

    def test_a_fall_reads_as_good_news(self):
        make_series(self.pechay, [
            (date(2026, 8, 3), "150.00"), (date(2026, 8, 11), "100.00"),
        ])
        movement = movements([self.pechay])[self.pechay.pk]
        self.assertEqual(movement.direction, "down")
        self.assertEqual(movement.badge_class, "badge-success")

    def test_a_commodity_with_no_prices_reports_nothing_not_zero(self):
        movement = movements([self.pechay])[self.pechay.pk]
        self.assertIsNone(movement.latest)
        self.assertIsNone(movement.week_pct)
        self.assertEqual(movement.points, 0)

    def test_resolution_is_a_fixed_query_count(self):
        for index in range(20):
            other = Commodity.objects.create(
                category=CommodityCategory.FISH, name=f"Fish {index}"
            )
            make_series(other, [(date(2026, 8, 11), "200.00")])

        # Materialised outside the assertion: a queryset is lazy, so leaving
        # it inside would count the caller's own fetch against the resolver.
        everything = list(Commodity.objects.all())

        # One for the latest-day lookup, one for the window. A per-commodity
        # lookup here would be a query per table row.
        with self.assertNumQueries(2):
            movements(everything)


class BiggestMoverTests(TestCase):
    def test_both_tails_are_returned(self):
        rising = Commodity.objects.create(
            category=CommodityCategory.LOWLAND_VEG, name="Lettuce"
        )
        falling = Commodity.objects.create(
            category=CommodityCategory.POULTRY, name="Chicken Thigh"
        )
        make_series(rising, [(date(2026, 8, 3), "100.00"),
                             (date(2026, 8, 7), "150.00"),
                             (date(2026, 8, 11), "200.00")])
        make_series(falling, [(date(2026, 8, 3), "200.00"),
                              (date(2026, 8, 7), "150.00"),
                              (date(2026, 8, 11), "100.00")])

        names = [row["commodity"].name for row in biggest_movers(limit=2)]

        # A shopper wants the bargain and the thing to avoid, not one end.
        self.assertIn("Chicken Thigh", names)
        self.assertIn("Lettuce", names)
        self.assertEqual(names[0], "Chicken Thigh")

    def test_a_commodity_with_too_little_history_is_left_out(self):
        thin = Commodity.objects.create(
            category=CommodityCategory.FISH, name="Bangus"
        )
        make_series(thin, [(date(2026, 8, 11), "268.27")])
        self.assertEqual(biggest_movers(), [])


class ChartGeometryTests(TestCase):
    def setUp(self):
        self.commodity = Commodity.objects.create(
            category=CommodityCategory.PORK, name="Pork Belly"
        )

    def _series(self, prices):
        make_series(self.commodity, [
            (date(2026, 8, 3 + index), price) for index, price in enumerate(prices)
        ])
        return list(self.commodity.prices.order_by("observed_on"))

    def test_a_rising_series_slopes_upward_on_screen(self):
        chart = build_chart(self._series(["100.00", "200.00"]))
        # SVG y grows downward, so a higher price is a smaller y.
        self.assertLess(chart.points[-1].y, chart.points[0].y)
        self.assertTrue(chart.has_shape)

    def test_a_flat_series_is_centred_rather_than_dividing_by_zero(self):
        chart = build_chart(self._series(["100.00", "100.00", "100.00"]))

        # Drawn to full scale, a flat line would turn rounding noise into a
        # mountain range.
        self.assertEqual(len({point.y for point in chart.points}), 1)
        self.assertEqual(chart.low, chart.high)

    def test_one_reading_is_not_a_trend(self):
        self.assertFalse(build_chart(self._series(["100.00"])).has_shape)

    def test_an_empty_series_yields_no_chart(self):
        self.assertIsNone(build_chart([]))

    def test_every_point_stays_inside_the_viewbox(self):
        chart = build_chart(self._series(["10.00", "500.00", "250.00"]))
        for point in chart.points:
            self.assertTrue(0 <= point.x <= 720, point)
            self.assertTrue(0 <= point.y <= 220, point)


class GroceryScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("shopper", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_the_list_renders_with_no_data(self):
        self.assertEqual(
            self.client.get(reverse("grocery:commodities")).status_code, 200
        )
        self.assertEqual(self.client.get(reverse("grocery:map")).status_code, 200)

    @override_settings(
        MAP_TILE_URL="https://tiles.example.test/{z}/{x}/{y}.png",
        MAP_TILE_ATTRIBUTION="Example tiles",
        MAP_TILE_MAX_ZOOM=17,
    )
    def test_the_map_uses_the_configured_tile_provider(self):
        response = self.client.get(reverse("grocery:map"))

        self.assertContains(response, "https://tiles.example.test/{z}/{x}/{y}.png")
        self.assertContains(response, "Example tiles")
        self.assertContains(response, "maxZoom: 17")

    def test_the_screens_render_with_a_series(self):
        commodity = Commodity.objects.create(
            category=CommodityCategory.PORK, name="Pork Belly (Liempo), Local"
        )
        make_series(commodity, [
            (date(2026, 8, 3), "379.51"),
            (date(2026, 8, 10), "381.44"),
            (date(2026, 8, 17), "380.84"),
        ])

        self.assertEqual(
            self.client.get(reverse("grocery:commodities")).status_code, 200
        )
        detail = self.client.get(
            reverse("grocery:commodity_detail", args=[commodity.pk])
        )
        self.assertEqual(detail.status_code, 200)
        # The chart and the table are the same numbers, and the table is the
        # accessible view of the chart rather than an extra.
        self.assertContains(detail, "polyline")
        self.assertContains(detail, "379.51")

    def test_the_overview_survives_grocery_data_being_present(self):
        commodity = Commodity.objects.create(
            category=CommodityCategory.LOWLAND_VEG, name="Pechay"
        )
        make_series(commodity, [(date(2026, 8, 3), "94.81"),
                                (date(2026, 8, 7), "150.00"),
                                (date(2026, 8, 11), "213.14")])
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 200)

    def test_the_filter_returns_rows_only_to_htmx(self):
        Commodity.objects.create(category=CommodityCategory.FISH, name="Bangus")
        partial = self.client.get(
            reverse("grocery:commodities"), {"q": "bangus"},
            headers={"HX-Request": "true"},
        )
        self.assertEqual(partial.status_code, 200)
        self.assertNotContains(partial, "<html")

    def test_tracking_refuses_an_offsite_return(self):
        commodity = Commodity.objects.create(
            category=CommodityCategory.FISH, name="Bangus"
        )
        response = self.client.post(
            reverse("grocery:commodity_track", args=[commodity.pk]),
            {"next": "https://example.com/phish"},
        )
        self.assertNotIn("example.com", response["Location"])

        commodity.refresh_from_db()
        self.assertTrue(commodity.is_tracked)

    def test_signed_out_users_reach_nothing(self):
        self.client.logout()
        for name in ("grocery:commodities", "grocery:map"):
            with self.subTest(screen=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login/", response["Location"])


class GroceryMapEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("shopper", password="not-a-real-password")
        self.client.force_login(self.user)
        self.url = reverse("grocery:stores_json")
        self.box = {"south": 14.5, "west": 121.0, "north": 14.7, "east": 121.2}

    def test_only_supermarkets_in_the_box_are_returned(self):
        make_store(name="Puregold", brand="Puregold", osm_id=1)
        make_store(
            kind=PlaceKind.CONVENIENCE, name="7-Eleven", brand="7-Eleven", osm_id=2
        )
        make_store(
            name="Outside", brand="Puregold", osm_id=3,
            latitude=Decimal("10.300000"), longitude=Decimal("123.900000"),
        )

        payload = self.client.get(self.url, self.box).json()

        self.assertEqual([store["name"] for store in payload["stores"]], ["Puregold"])

    def test_store_group_filters_to_the_requested_chains(self):
        make_store(name="SM Hypermarket", brand="SM Hypermarket", osm_id=1)
        make_store(name="Puregold", brand="Puregold", osm_id=2)
        make_store(name="Robinsons Easymart", brand="Robinsons Easymart", osm_id=3)

        payload = self.client.get(
            self.url, {**self.box, "store_group": "robinsons"}
        ).json()

        self.assertEqual([store["brand"] for store in payload["stores"]], ["Robinsons Easymart"])

    def test_known_item_prices_sort_cheapest_first(self):
        puregold = make_store(name="Puregold", brand="Puregold", osm_id=1)
        robinsons = make_store(
            name="Robinsons Supermarket", brand="Robinsons Supermarket", osm_id=2,
            latitude=Decimal("14.581000"), longitude=Decimal("121.061000"),
        )
        log_item(puregold, "Chicken breast", "210.00")
        log_item(robinsons, "Chicken breast", "190.00")

        payload = self.client.get(
            self.url, {**self.box, "item": "chicken"}
        ).json()["stores"]

        self.assertEqual(payload[0]["brand"], "Robinsons Supermarket")
        self.assertEqual(payload[0]["difference_vs_best"], "0.00")
        self.assertEqual(payload[1]["difference_vs_best"], "20.00")

    def test_da_reference_is_returned_when_the_item_is_tracked(self):
        chicken = Commodity.objects.create(
            category=CommodityCategory.POULTRY,
            name="Chicken Breast",
            unit="kg",
        )
        make_series(chicken, [(date(2026, 8, 17), "220.00")])
        make_store(name="Puregold", brand="Puregold", osm_id=1)

        payload = self.client.get(self.url, {**self.box, "item": "chicken"}).json()

        self.assertEqual(payload["reference"]["commodity"], "Chicken Breast")
        self.assertEqual(payload["reference"]["price"], "220.00")

    def test_missing_bounding_box_is_bad_request(self):
        self.assertEqual(self.client.get(self.url).status_code, 400)

    def test_signed_out_callers_are_redirected(self):
        self.client.logout()
        response = self.client.get(self.url, self.box)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
