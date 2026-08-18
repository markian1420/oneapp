"""Import per-brand fuel averages from the MetroFuel Tracker prices page."""

from __future__ import annotations

import httpx
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.fuel.metrofuel import PRICES_URL, parse
from apps.fuel.models import DOEAdvisory
from apps.fuel.services import week_start
from apps.places.brands import normalise_brand

REQUEST_TIMEOUT = 45
SOURCE_NAME = "MetroFuel Tracker, national brand average"


class Command(BaseCommand):
    help = (
        "Import per-brand fuel averages from MetroFuel Tracker. Gives a Shell "
        "station Shell's number instead of the whole region's median. Reads "
        "only the public /prices page, which their robots.txt allows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--region", default="NCR",
            help="Region to file the brand rows under.",
        )
        parser.add_argument(
            "--week-of", default="",
            help="Monday of the week to file under. Defaults to this week.",
        )
        parser.add_argument(
            "--national", action="store_true",
            help="Also write the national averages as a blank-brand row. Off "
                 "by default: a regional survey is a better prevailing figure "
                 "than a national one.",
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

        self.stdout.write("Reading MetroFuel Tracker... ", ending="")
        try:
            response = httpx.get(
                PRICES_URL,
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "OneApp/1.0 (personal budgeting app)"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self.stdout.write(self.style.ERROR("failed"))
            raise CommandError(f"Could not read the page: {exc}") from exc

        parsed = parse(response.text)
        if not parsed.brands:
            raise CommandError(
                "No brand prices found - the page structure has changed."
            )

        self.stdout.write(self.style.SUCCESS(
            f"{parsed.stations} stations across {parsed.cities} cities"
            + (f", updated {parsed.updated_on}" if parsed.updated_on else "")
        ))

        written = unpublished = 0
        with transaction.atomic():
            for entry in parsed.brands:
                if not entry.prices:
                    # Cleanfuel is listed with stations but no prices. Writing
                    # a neighbouring brand's figure here would be invisible and
                    # wrong, so it is skipped and counted out loud.
                    self.stdout.write(
                        f"  {entry.brand:<12} no prices published, skipped"
                    )
                    unpublished += 1
                    continue

                brand = normalise_brand(entry.brand)
                for grade, price in entry.prices.items():
                    self.stdout.write(
                        f"  {brand:<12} {grade:<8} {price}"
                    )
                    if options["dry_run"]:
                        continue

                    DOEAdvisory.objects.update_or_create(
                        week_of=week, region=region, brand=brand,
                        fuel_type=grade,
                        defaults={
                            "price": price,
                            "sample_size": entry.stations,
                            "source_url": PRICES_URL,
                            "source_note": SOURCE_NAME,
                            "fetched_at": timezone.now(),
                        },
                    )
                    written += 1

            if options["national"] and parsed.national and not options["dry_run"]:
                for grade, price in parsed.national.items():
                    DOEAdvisory.objects.update_or_create(
                        week_of=week, region=region, brand="", fuel_type=grade,
                        defaults={
                            "price": price,
                            "sample_size": parsed.stations,
                            "source_url": PRICES_URL,
                            "source_note": "MetroFuel Tracker, national average",
                            "fetched_at": timezone.now(),
                        },
                    )
                    written += 1

            if options["dry_run"]:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS(f"\n{written} row(s) written."))
        if unpublished:
            self.stdout.write(
                f"{unpublished} brand(s) list stations but publish no price."
            )
        self.stdout.write(
            "These are national brand averages, so they are a brand signal "
            "rather than a local one. Anything you log yourself outranks them."
        )
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run - nothing was written."))
