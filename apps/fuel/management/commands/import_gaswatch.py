"""
Import the NCR pump price survey published by GasWatch PH.

Why this is worth having: the DOE bulletin gives one figure per brand, and its
own site has been returning HTTP 500 on every endpoint. GasWatch publishes a
JSON endpoint covering 1,292 Metro Manila stations across five grades, and its
robots.txt allows it.

What it is not: a per-station price for the map. The payload keys stations by
an opaque numeric id with no name, brand or coordinates anywhere on the site,
so there is no honest way to attach a figure to a particular pump. GasWatch
themselves describe the values as derived from the weekly DOE advisory rather
than observed at the pump.

So it is imported as what it actually is - a regional price band. The median
becomes the prevailing price for Metro Manila, which finally gives every station
in the area a number. The spread is imported alongside it, because the gap between the
cheapest and dearest pump is the entire argument for comparing at all: nearly
23 pesos a litre on unleaded, which is over 900 pesos on a tank.

Attribution is recorded on every row. There is no stated reuse licence, so this
is for personal use.
"""

from __future__ import annotations

import statistics
from decimal import Decimal

import httpx
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.fuel.models import DOEAdvisory, FuelType
from apps.fuel.services import week_start

SOURCE_URL = "https://gaswatchph.com/api/prices"
SOURCE_NAME = "GasWatch PH survey"
REQUEST_TIMEOUT = 45

# Their grade names, mapped onto the app's.
GRADE_MAP = {
    "unleaded": FuelType.GAS_91,
    "premium95": FuelType.GAS_95,
    "premium97": FuelType.GAS_97,
    "diesel": FuelType.DIESEL,
    "premiumDiesel": FuelType.DIESEL_PREMIUM,
}

# Below this the median is not a market figure, it is an anecdote.
MIN_SAMPLE = 20


class Command(BaseCommand):
    help = (
        "Import the GasWatch PH pump price survey as a regional price band. "
        "Gives every station a baseline where the DOE bulletin is unavailable."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--region", default="NCR",
            help="Region the survey covers. GasWatch is Metro Manila only.",
        )
        parser.add_argument(
            "--week-of", default="",
            help="Monday of the week to file it under. Defaults to this week.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        region = options["region"]

        if options["week_of"]:
            from datetime import datetime
            try:
                week = week_start(
                    datetime.strptime(options["week_of"], "%Y-%m-%d").date()
                )
            except ValueError as exc:
                raise CommandError("--week-of must be YYYY-MM-DD") from exc
        else:
            week = week_start()

        self.stdout.write("Reading the GasWatch survey... ", ending="")
        try:
            response = httpx.get(
                SOURCE_URL,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": "OneApp/1.0 (personal budgeting app)",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self.stdout.write(self.style.ERROR("failed"))
            raise CommandError(f"Could not read GasWatch: {exc}") from exc

        stations = payload.get("overrides") or {}
        if not stations:
            raise CommandError(
                "No station prices in the payload - the format has changed."
            )
        self.stdout.write(self.style.SUCCESS(f"{len(stations)} stations"))

        samples: dict[str, list[Decimal]] = {}
        for entry in stations.values():
            if not isinstance(entry, dict):
                continue
            for raw_grade, info in entry.items():
                grade = GRADE_MAP.get(raw_grade)
                if not grade or not isinstance(info, dict):
                    continue
                price = info.get("p")
                if price:
                    samples.setdefault(grade, []).append(Decimal(str(price)))

        written = skipped = 0
        with transaction.atomic():
            for grade, prices in sorted(samples.items()):
                if len(prices) < MIN_SAMPLE:
                    self.stdout.write(self.style.WARNING(
                        f"  {grade}: only {len(prices)} readings, skipped"
                    ))
                    skipped += 1
                    continue

                prices.sort()
                median = Decimal(str(statistics.median(prices))).quantize(
                    Decimal("0.001")
                )
                low, high = prices[0], prices[-1]

                self.stdout.write(
                    f"  {grade:<15} median {median:>7} "
                    f"(range {low}-{high}, {len(prices)} stations)"
                )

                if options["dry_run"]:
                    continue

                DOEAdvisory.objects.update_or_create(
                    week_of=week,
                    region=region,
                    brand="",          # a market median belongs to no brand
                    fuel_type=grade,
                    defaults={
                        "price": median,
                        "low": low,
                        "high": high,
                        "sample_size": len(prices),
                        "source_url": SOURCE_URL,
                        "source_note": SOURCE_NAME,
                        "fetched_at": timezone.now(),
                    },
                )
                written += 1

            if options["dry_run"]:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS(
            f"\n{written} grade(s) written for {region}, week of {week}."
        ))
        if skipped:
            self.stdout.write(f"{skipped} skipped for too small a sample.")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run - nothing was written."))
        else:
            self.stdout.write(
                "Filed as a Metro Manila regional median. "
                "Anything you log yourself still outranks it."
            )
