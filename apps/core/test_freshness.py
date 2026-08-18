"""
Tests for data freshness.

The claim being defended is a modest one: the app never says a number is
current when its source has moved on. So these mostly check that staleness is
noticed and that a failure is distinguishable from nobody having run anything.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .freshness import BY_KEY, SOURCES, statuses, summary
from .models import SourceRun


def log(source="fuel_metrofuel", hours_ago=0, ok=True, rows=10) -> SourceRun:
    run = SourceRun.objects.create(source=source, ok=ok, rows=rows)
    if hours_ago:
        SourceRun.objects.filter(pk=run.pk).update(
            ran_at=timezone.now() - timedelta(hours=hours_ago)
        )
        run.refresh_from_db()
    return run


class SourceRegistryTests(TestCase):
    def test_every_source_names_a_command_that_exists(self):
        from django.core.management import get_commands

        available = get_commands()
        for source in SOURCES:
            with self.subTest(source=source.key):
                self.assertIn(source.command.split()[0], available)

    def test_due_always_comes_before_stale(self):
        for source in SOURCES:
            with self.subTest(source=source.key):
                self.assertLess(source.due_after_hours, source.stale_after_hours)


class StatusTests(TestCase):
    def test_a_source_never_fetched_says_so(self):
        state = {s.source.key: s for s in statuses()}["fuel_metrofuel"]

        self.assertEqual(state.state, "never")
        self.assertEqual(state.age_label, "never")

    def test_a_recent_run_is_current(self):
        log(hours_ago=1)
        self.assertEqual(
            {s.source.key: s for s in statuses()}["fuel_metrofuel"].state, "current"
        )

    def test_past_its_cadence_it_is_due(self):
        # MetroFuel publishes daily; two days on it is behind.
        log(hours_ago=48)
        self.assertEqual(
            {s.source.key: s for s in statuses()}["fuel_metrofuel"].state, "due"
        )

    def test_long_past_its_cadence_it_is_stale(self):
        log(hours_ago=24 * 10)
        self.assertEqual(
            {s.source.key: s for s in statuses()}["fuel_metrofuel"].state, "stale"
        )

    def test_a_failed_run_is_distinguishable_from_never_running(self):
        # One means the source is down, the other means nobody tried. They
        # need different answers.
        log(ok=False)
        state = {s.source.key: s for s in statuses()}["fuel_metrofuel"]

        self.assertEqual(state.state, "failed")
        self.assertNotEqual(state.state, "never")

    def test_the_most_recent_run_wins(self):
        log(hours_ago=200)
        log(hours_ago=1)
        self.assertEqual(
            {s.source.key: s for s in statuses()}["fuel_metrofuel"].state, "current"
        )

    def test_each_source_is_judged_on_its_own_cadence(self):
        # Four days is behind for a daily feed and perfectly fine for a
        # weekly one.
        log(source="fuel_metrofuel", hours_ago=24 * 4)
        log(source="fuel_gaswatch", hours_ago=24 * 4)

        state = {s.source.key: s for s in statuses()}
        self.assertEqual(state["fuel_metrofuel"].state, "due")
        self.assertEqual(state["fuel_gaswatch"].state, "current")

    def test_the_summary_counts_what_needs_attention(self):
        log(source="fuel_metrofuel", hours_ago=1)
        log(source="fuel_gaswatch", hours_ago=24 * 20)

        result = summary()
        self.assertEqual(result["current"], 1)
        self.assertIn("fuel_gaswatch",
                      [s.source.key for s in result["attention"]])


class RefreshCommandTests(TestCase):
    def _run(self, **options):
        out = StringIO()
        with mock.patch("apps.core.management.commands.refresh_all.call_command") as ran:
            call_command("refresh_all", stdout=out, stderr=StringIO(), **options)
        return out.getvalue(), ran

    def test_it_records_a_run_for_each_source(self):
        self._run()
        # Places are left out of the routine refresh on purpose.
        self.assertEqual(SourceRun.objects.count(), len(SOURCES) - 1)
        self.assertFalse(SourceRun.objects.filter(source="places_osm").exists())

    def test_places_can_be_asked_for(self):
        self._run(places=True)
        self.assertTrue(SourceRun.objects.filter(source="places_osm").exists())

    def test_one_source_failing_does_not_stop_the_others(self):
        out = StringIO()

        def flaky(name, *args, **kwargs):
            if name == "import_metrofuel":
                raise RuntimeError("source is down")

        with mock.patch("apps.core.management.commands.refresh_all.call_command",
                        side_effect=flaky):
            call_command("refresh_all", stdout=out, stderr=StringIO())

        self.assertFalse(SourceRun.objects.get(source="fuel_metrofuel").ok)
        self.assertTrue(SourceRun.objects.get(source="fuel_gaswatch").ok)
        self.assertIn("failed", out.getvalue())

    def test_due_only_skips_what_is_already_current(self):
        for source in SOURCES:
            log(source=source.key, hours_ago=1)

        out, ran = self._run(due_only=True)
        self.assertIn("Everything is current", out)
        ran.assert_not_called()

    def test_due_only_still_refreshes_what_is_behind(self):
        for source in SOURCES:
            log(source=source.key, hours_ago=1)
        SourceRun.objects.filter(source="grocery_da").update(
            ran_at=timezone.now() - timedelta(days=9)
        )

        _, ran = self._run(due_only=True)
        self.assertEqual(ran.call_count, 1)

    def test_a_single_source_can_be_targeted(self):
        _, ran = self._run(only=["fuel_gaswatch"])

        self.assertEqual(ran.call_count, 1)
        self.assertEqual(SourceRun.objects.count(), 1)

    def test_an_unknown_source_is_refused(self):
        out = StringIO()
        errors = StringIO()
        call_command("refresh_all", only=["nonsense"], stdout=out, stderr=errors)

        self.assertIn("Unknown source", errors.getvalue())
        self.assertEqual(SourceRun.objects.count(), 0)


class FreshnessScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("driver", password="not-a-real-password")
        self.client.force_login(self.user)

    def test_the_briefing_shows_how_current_each_source_is(self):
        log(source="fuel_metrofuel", hours_ago=1)
        response = self.client.get(reverse("insights:briefing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data freshness")
        self.assertContains(response, "Nothing here is live")

    def test_it_renders_with_nothing_ever_fetched(self):
        response = self.client.get(reverse("insights:briefing"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["freshness"]["current"], 0)
