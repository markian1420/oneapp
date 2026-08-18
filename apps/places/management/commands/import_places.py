"""Import places from OpenStreetMap via the Overpass API."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.places.brands import normalise_brand
from apps.places.models import Place, PlaceKind
from apps.places.overpass import (
    KIND_SELECTORS,
    OverpassError,
    bbox_area,
    element_coordinates,
    fetch_area,
    resolve_areas,
)
from apps.places.regions import region_for


class Command(BaseCommand):
    help = (
        "Import or refresh places from OpenStreetMap. Defaults to fuel "
        "stations in Metro Manila. Pass --kind repeatedly for more, or "
        "--kind all for every kind the app knows about."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            action="append",
            default=[],
            metavar="KIND",
            help=(
                "One of: " + ", ".join(KIND_SELECTORS) + ", or all. "
                "Repeat for several. Defaults to fuel."
            ),
        )
        parser.add_argument(
            "--area",
            action="append",
            default=[],
            metavar="AREA",
            help=(
                "ISO code (PH-RIZ), province name (Rizal), NCR, or all. "
                "Repeat for several. Defaults to NCR."
            ),
        )
        parser.add_argument(
            "--bbox",
            default="",
            metavar="S,W,N,E",
            help=(
                "Import a plain bounding box instead of an administrative "
                "area. Cheaper for Overpass to answer when the public "
                "instances are loaded, but leaves province and region blank."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        kinds = self._resolve_kinds(options["kind"])

        try:
            if options["bbox"]:
                areas = [bbox_area(options["bbox"])]
            else:
                areas = resolve_areas(options["area"] or ["NCR"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        dry_run = options["dry_run"]
        totals = {"created": 0, "updated": 0, "skipped": 0}
        steps = [(area, kind) for area in areas for kind in kinds]

        for index, (area, kind) in enumerate(steps, start=1):
            label = PlaceKind(kind).label
            self.stdout.write(
                f"[{index}/{len(steps)}] {area.name} · {label}... ", ending=""
            )
            try:
                elements = fetch_area(area, kind)
            except OverpassError as exc:
                # One unavailable combination should not discard the ones that
                # did come back, so this is reported and stepped over.
                self.stdout.write(self.style.ERROR("failed"))
                self.stderr.write(f"    {exc}")
                continue

            result = self._ingest(area, kind, elements, dry_run=dry_run)
            for key in totals:
                totals[key] += result[key]

            self.stdout.write(
                self.style.SUCCESS(
                    f"{len(elements)} found, {result['created']} new, "
                    f"{result['updated']} updated"
                    + (f", {result['skipped']} skipped" if result["skipped"] else "")
                )
            )

        verb = "would be " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {totals['created']} {verb}created, "
                f"{totals['updated']} {verb}updated, "
                f"{totals['skipped']} skipped for want of coordinates."
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run - nothing was written."))

    # ------------------------------------------------------------------

    def _resolve_kinds(self, requested: list[str]) -> list[str]:
        if not requested:
            return [PlaceKind.FUEL.value]
        if any(kind.lower() == "all" for kind in requested):
            return list(KIND_SELECTORS)

        valid = set(KIND_SELECTORS)
        chosen, unknown = [], []
        for kind in requested:
            key = kind.strip().lower()
            (chosen if key in valid else unknown).append(key)

        if unknown:
            raise CommandError(
                "Unknown kind(s): " + ", ".join(unknown)
                + ". Choose from: " + ", ".join(sorted(valid)) + ", or all."
            )
        return chosen

    def _ingest(self, area, kind: str, elements, *, dry_run: bool) -> dict[str, int]:
        created = updated = skipped = 0
        now = timezone.now()

        with transaction.atomic():
            for element in elements:
                coordinates = element_coordinates(element)
                if not coordinates:
                    # A way whose nodes fall outside the extract has no centre.
                    skipped += 1
                    continue

                latitude, longitude = coordinates
                tags = element.get("tags", {})
                raw_brand = (
                    tags.get("brand") or tags.get("operator") or tags.get("name") or ""
                )
                city = (
                    tags.get("addr:city")
                    or tags.get("addr:municipality")
                    or tags.get("addr:town")
                    or ""
                )

                values = {
                    "name": tags.get("name", "")[:200],
                    "brand": normalise_brand(raw_brand),
                    "brand_raw": raw_brand[:120],
                    "latitude": round(latitude, 6),
                    "longitude": round(longitude, 6),
                    "street": tags.get("addr:street", "")[:200],
                    "city": city[:120],
                    # Province and region come from the area queried, not from
                    # tags: only 13% of Philippine places carry addr:province,
                    # but everything inside Rizal's boundary is in Rizal.
                    "province": area.province or tags.get("addr:province", "")[:120],
                    "region": area.region or region_for(
                        province=tags.get("addr:province", ""), city=city
                    ),
                    "opening_hours": tags.get("opening_hours", "")[:200],
                    "last_seen_at": now,
                }

                if dry_run:
                    exists = Place.objects.filter(
                        kind=kind, osm_type=element["type"], osm_id=element["id"]
                    ).exists()
                    updated += 1 if exists else 0
                    created += 0 if exists else 1
                    continue

                _, was_created = Place.objects.update_or_create(
                    kind=kind,
                    osm_type=element["type"],
                    osm_id=element["id"],
                    defaults=values,
                )
                created += 1 if was_created else 0
                updated += 0 if was_created else 1

            if dry_run:
                transaction.set_rollback(True)

        return {"created": created, "updated": updated, "skipped": skipped}
