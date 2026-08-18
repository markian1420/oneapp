"""Import the DA Daily Price Index for NCR."""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta

import httpx
import pdfplumber
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.grocery.da_index import daily_index_url, parse
from apps.grocery.models import Commodity, CommodityPrice, PriceSource

REQUEST_TIMEOUT = 60


class Command(BaseCommand):
    help = (
        "Import the DA Daily Price Index for NCR. Defaults to the most recent "
        "weekday. Use --backfill to pull a run of past days in one go."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            default="",
            metavar="YYYY-MM-DD",
            help="The day to import. Defaults to the most recent weekday.",
        )
        parser.add_argument(
            "--backfill",
            type=int,
            default=0,
            metavar="DAYS",
            help="Also import this many days before --date, building history.",
        )
        parser.add_argument(
            "--file",
            default="",
            help="Read a downloaded PDF instead of fetching. Implies one day.",
        )

    def handle(self, *args, **options):
        if options["file"]:
            with pdfplumber.open(options["file"]) as pdf:
                parsed = parse(pdf)
            if not parsed.published_on:
                raise CommandError(
                    f"{options['file']} carries no readable date in its title - "
                    "is it a DA Daily Price Index?"
                )
            self._store(parsed, source_url="")
            return

        if options["date"]:
            try:
                target = datetime.strptime(options["date"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError("--date must be YYYY-MM-DD") from exc
        else:
            target = self._latest_weekday()

        days = [target - timedelta(days=offset)
                for offset in range(options["backfill"] + 1)]

        imported = 0
        for day in days:
            # The DA does not publish at weekends, so asking for one is a
            # guaranteed 404 rather than a gap worth reporting.
            if day.weekday() >= 5:
                self.stdout.write(f"{day}: weekend, not published - skipped")
                continue
            if self._import_day(day):
                imported += 1

        # Unconditional: removing commodities that have no price behind them
        # is correct whether or not this run fetched anything, and tying it to
        # a successful fetch means a run that imports nothing leaves the
        # strandings from the previous parser version in place.
        self._prune_orphans()

        self.stdout.write(
            self.style.SUCCESS(f"\n{imported} day(s) imported.")
            if imported
            else self.style.WARNING("\nNothing imported.")
        )

    def _prune_orphans(self) -> None:
        """Drop commodities that no longer have a single price behind them.

        A parser fix changes how names come out, which strands whatever the
        previous version created. Without this they linger for ever as
        plausible-looking commodities with no data, and the only clue is a
        commodity count that does not match the source.
        """
        orphans = Commodity.objects.filter(prices__isnull=True)
        count = orphans.count()
        if count:
            orphans.delete()
            self.stdout.write(
                self.style.WARNING(f"Pruned {count} commodity record(s) with no prices.")
            )

    # ------------------------------------------------------------------

    def _latest_weekday(self) -> date:
        day = timezone.localdate()
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day

    def _import_day(self, day: date) -> bool:
        url = daily_index_url(day)
        self.stdout.write(f"{day}: fetching... ", ending="")

        try:
            response = httpx.get(
                url,
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "OneApp/1.0 (personal grocery price tracker)"},
            )
        except httpx.HTTPError as exc:
            self.stdout.write(self.style.ERROR("failed"))
            self.stderr.write(f"    {exc}")
            return False

        if response.status_code != 200:
            # A 404 usually means "not published yet", which on the day itself
            # is normal rather than broken.
            self.stdout.write(self.style.WARNING(f"HTTP {response.status_code}"))
            return False

        if not response.content.startswith(b"%PDF"):
            self.stdout.write(self.style.ERROR("not a PDF"))
            self.stderr.write(f"    {url} returned {len(response.content)} bytes of non-PDF")
            return False

        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            parsed = parse(pdf)

        if parsed.published_on and parsed.published_on != day:
            # The DA has reposted the wrong file under a date before. Trust the
            # title inside the document over the filename it was fetched from.
            self.stdout.write(
                self.style.WARNING(f"file says {parsed.published_on}, filed under {day}")
            )

        self._store(parsed, source_url=url)
        return True

    def _store(self, parsed, *, source_url: str) -> None:
        day = parsed.published_on
        created = updated = 0

        with transaction.atomic():
            for row in parsed.rows:
                commodity, _ = Commodity.objects.get_or_create(
                    category=row.category,
                    name=row.name,
                    specification=row.specification,
                    defaults={"unit": _unit_for(row)},
                )
                _, was_created = CommodityPrice.objects.update_or_create(
                    commodity=commodity,
                    region=parsed.region,
                    observed_on=day,
                    source=PriceSource.DA_DAILY,
                    defaults={
                        "price": row.price,
                        "source_url": source_url,
                        "imported_at": timezone.now(),
                    },
                )
                created += 1 if was_created else 0
                updated += 0 if was_created else 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{len(parsed.rows)} rows ({created} new, {updated} updated), "
                f"{parsed.unavailable} n/a"
                + (f", {parsed.dropped} DROPPED" if parsed.dropped else "")
            )
        )


def _unit_for(row) -> str:
    """The DA's footnote on units, applied.

    Everything is priced per kilogram except eggs, which go per piece, and
    cooking oil, which goes by volume. Getting this wrong makes a litre of oil
    look like a bargain against a kilo of pork.
    """
    name = row.name.lower()
    if "egg" in name:
        return "piece"
    if "cooking oil" in name:
        return row.specification or "bottle"
    return "kg"
