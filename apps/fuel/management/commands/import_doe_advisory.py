"""
Load a DOE weekly retail price advisory.

The DOE does not publish an API. It puts out a spreadsheet, and as of this
writing the per-station dashboard that used to live on legacy.doe.gov.ph does
not resolve at all. So the dependable path here is a file you downloaded, and
the network fetch is a convenience that says plainly when it cannot get one
rather than quietly leaving the baseline stale.
"""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.places.brands import normalise_brand
from apps.fuel.models import DOEAdvisory, FuelType
from apps.places.regions import REGION_NAMES
from apps.fuel.services import week_start

# Column headings accepted for each field, lowercased. Spreadsheets from the
# DOE and the ones people retype from them do not agree on wording.
COLUMN_ALIASES = {
    "region": {"region", "area", "region_code"},
    "brand": {"brand", "company", "oil company", "retailer"},
    "fuel_type": {"fuel_type", "fuel", "product", "grade"},
    "price": {"price", "prevailing price", "retail price", "price_per_liter", "php"},
}

# Free-text product names seen in advisories, mapped to our grades.
FUEL_ALIASES = {
    "gas_91": {"gas_91", "ron91", "ron 91", "gasoline ron 91", "unleaded",
               "unleaded 91", "regular", "rug", "gasoline 91"},
    "gas_95": {"gas_95", "ron95", "ron 95", "gasoline ron 95", "premium 95",
               "gasoline 95"},
    "gas_97": {"gas_97", "ron97", "ron 97", "gasoline ron 97", "premium",
               "premium 97", "gasoline 97"},
    "diesel": {"diesel", "auto diesel", "automotive diesel", "add"},
    "diesel_premium": {"diesel_premium", "premium diesel", "diesel premium",
                       "euro 5 diesel"},
}


def _canonical_fuel(value: str) -> str:
    key = " ".join(value.lower().split())
    for fuel_type, aliases in FUEL_ALIASES.items():
        if key in aliases:
            return fuel_type
    return ""


class Command(BaseCommand):
    help = (
        "Load a DOE weekly price advisory from a CSV or XLSX file. "
        "Columns: region, brand, fuel_type, price. A blank brand means the "
        "region's prevailing price."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            required=True,
            help="Path to the advisory CSV or XLSX.",
        )
        parser.add_argument(
            "--week-of",
            default="",
            help="Monday of the week the advisory covers (YYYY-MM-DD). "
                 "Defaults to the current week.",
        )
        parser.add_argument(
            "--source-url",
            default="",
            help="Where the file came from, recorded against each row.",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"No such file: {path}")

        if options["week_of"]:
            try:
                week = week_start(
                    datetime.strptime(options["week_of"], "%Y-%m-%d").date()
                )
            except ValueError as exc:
                raise CommandError("--week-of must be YYYY-MM-DD") from exc
        else:
            week = week_start()

        rows = self._read(path)
        if not rows:
            raise CommandError(f"{path} has no data rows.")

        created, updated, rejected = self._load(
            rows, week=week, source_url=options["source_url"], source=path.name
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Week of {week}: {created} advisory rows created, {updated} updated."
            )
        )
        if rejected:
            self.stdout.write(
                self.style.WARNING(f"{len(rejected)} row(s) skipped:")
            )
            for line, reason in rejected[:20]:
                self.stderr.write(f"    row {line}: {reason}")
            if len(rejected) > 20:
                self.stderr.write(f"    ... and {len(rejected) - 20} more")

    # ------------------------------------------------------------------

    def _read(self, path: Path) -> list[dict]:
        if path.suffix.lower() in {".xlsx", ".xlsm"}:
            return self._read_xlsx(path)
        # utf-8-sig: Excel writes a BOM, and without this the first heading
        # arrives as "﻿region" and never matches an alias.
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [self._normalise_keys(row) for row in csv.DictReader(handle)]

    def _read_xlsx(self, path: Path) -> list[dict]:
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)

        try:
            headings = [str(cell or "").strip() for cell in next(rows)]
        except StopIteration:
            return []

        parsed = []
        for values in rows:
            if not any(v is not None and str(v).strip() for v in values):
                continue
            parsed.append(
                self._normalise_keys(
                    {headings[i]: values[i] for i in range(min(len(headings), len(values)))}
                )
            )
        workbook.close()
        return parsed

    def _normalise_keys(self, row: dict) -> dict:
        mapped = {}
        for raw_key, value in row.items():
            key = str(raw_key or "").strip().lower()
            for field, aliases in COLUMN_ALIASES.items():
                if key in aliases:
                    mapped[field] = value
                    break
        return mapped

    def _load(self, rows, *, week, source_url: str, source: str):
        created = updated = 0
        rejected: list[tuple[int, str]] = []
        valid_fuel = {choice.value for choice in FuelType}

        with transaction.atomic():
            for line, row in enumerate(rows, start=2):
                region = str(row.get("region") or "").strip().upper()
                if region not in REGION_NAMES:
                    rejected.append((line, f"unknown region {region or '(blank)'}"))
                    continue

                raw_fuel = str(row.get("fuel_type") or "").strip()
                fuel_type = raw_fuel if raw_fuel in valid_fuel else _canonical_fuel(raw_fuel)
                if not fuel_type:
                    rejected.append((line, f"unrecognised fuel {raw_fuel or '(blank)'}"))
                    continue

                raw_price = str(row.get("price") or "").strip().replace(",", "")
                raw_price = raw_price.lstrip("P₱ ").strip()
                try:
                    price = Decimal(raw_price)
                except (InvalidOperation, ValueError):
                    rejected.append((line, f"bad price {raw_price or '(blank)'}"))
                    continue
                if price <= 0:
                    rejected.append((line, f"non-positive price {price}"))
                    continue

                raw_brand = str(row.get("brand") or "").strip()
                # "Prevailing", "common" and a blank cell all mean the same
                # thing: this is the region's price, not one company's.
                brand = (
                    ""
                    if raw_brand.lower() in {"", "prevailing", "common", "all", "any"}
                    else normalise_brand(raw_brand)
                )

                _, was_created = DOEAdvisory.objects.update_or_create(
                    week_of=week,
                    region=region,
                    brand=brand,
                    fuel_type=fuel_type,
                    defaults={
                        "price": price,
                        "source_url": source_url,
                        "source_note": source[:200],
                        "fetched_at": timezone.now(),
                    },
                )
                created += 1 if was_created else 0
                updated += 0 if was_created else 1

        return created, updated, rejected
