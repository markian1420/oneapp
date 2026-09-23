"""
Import the Metro Manila pump price survey published by GasWatch PH.

Why this is worth having: the DOE bulletin gives one figure per brand, and its
own site has been returning HTTP 500 on every endpoint. GasWatch publishes 1,811
Metro Manila stations with a price per grade, refreshed far more often, and its
robots.txt allows it.

It arrives in two pieces. `js/data.js` is the station list - id, brand, name,
area, coordinates and a baked price block - and `/api/prices` carries the
overrides applied on top of it, which is where the current week's numbers land.
Both are needed: the API alone keys stations by an opaque id with no name or
position, which is what this importer used to read, and why it could only file
the survey as one regional median. With the station list, a price can go to the
pump it belongs to.

Matching is by position and brand: the nearest station on the map within 150
metres, carrying the same brand once both spellings are normalised. Brand has
to agree because forecourts sit in pairs on opposite corners, and putting
Shell's price on the Petron across the road is worse than showing no price at
all.

What it is not: a price anyone here saw. GasWatch derives its figures from
community reports and the DOE advisory - their own file says RON97 is computed
from RON95 - so the numbers land in their own table and their own tier, below
anything first-hand and above a brand-wide advisory. A station-specific figure
from last week beats a brand average from this one.

The regional median is still imported alongside, because three-quarters of the
map can be matched and the rest still needs a number.

Attribution is recorded on every row. There is no stated reuse licence, so this
is for personal use.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from datetime import datetime
from decimal import Decimal

import httpx
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.fuel.models import DOEAdvisory, FuelType, StationSurveyPrice
from apps.fuel.services import week_start
from apps.places.brands import normalise_brand
from apps.places.models import Place, PlaceKind

STATION_URL = "https://gaswatchph.com/js/data.js"
PRICES_URL = "https://gaswatchph.com/api/prices"
SOURCE_NAME = "GasWatch PH survey"
REQUEST_TIMEOUT = 60

# Their grade names, mapped onto the app's. Kerosene and e-gasoline are
# published too; the app has no grade for either, so they are left behind
# rather than bent into one that nearly fits.
GRADE_MAP = {
    "unleaded": FuelType.GAS_91,
    "premium95": FuelType.GAS_95,
    "premium97": FuelType.GAS_97,
    "diesel": FuelType.DIESEL,
    "premiumDiesel": FuelType.DIESEL_PREMIUM,
}

# How close a survey station has to be to a station on the map. Forecourts are
# tens of metres across and the two sources place them from different sources,
# so a hundred and fifty metres is generous without reaching the next junction.
MATCH_METRES = 150

# Below this the median is not a market figure, it is an anecdote.
MIN_SAMPLE = 20


def parse_stations(script: str) -> tuple[list[dict], str]:
    """The station list and the date the file carries.

    data.js is JavaScript, but GAS_STATIONS is a plain JSON array inside it, so
    the array is cut out by matching brackets and parsed as what it is. A regex
    over the whole file would be at the mercy of every apostrophe in a station
    name.
    """
    marker = "const GAS_STATIONS = "
    start = script.find(marker)
    if start == -1:
        raise CommandError(
            "No station list in data.js - the format has changed."
        )

    start += len(marker)
    depth = 0
    end = None
    for index, char in enumerate(script[start:], start):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = index + 1
                break

    if end is None:
        raise CommandError("The station list in data.js is not closed.")

    try:
        stations = json.loads(script[start:end])
    except ValueError as exc:
        raise CommandError(f"Could not read the station list: {exc}") from exc

    updated = ""
    match = re.search(r'const LAST_UPDATED = "([^"]+)"', script)
    if match:
        updated = match.group(1)

    return stations, updated


def survey_date(raw: str):
    """The survey's own date, or today when it does not say."""
    for pattern in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), pattern).date()
        except ValueError:
            continue
    return timezone.localdate()


def metres_between(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    """Flat-earth distance, which at city scale is off by centimetres."""
    dy = (lat_a - lat_b) * 111_320
    dx = (lng_a - lng_b) * 111_320 * math.cos(math.radians(lat_a))
    return math.hypot(dx, dy)


class Command(BaseCommand):
    help = (
        "Import the GasWatch PH survey: a price for each station it can be "
        "matched to, and a regional median for the stations it cannot."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--region", default="NCR",
            help="Region the survey covers. GasWatch is Metro Manila only.",
        )
        parser.add_argument(
            "--week-of", default="",
            help="Monday of the week to file the median under. Defaults to this week.",
        )
        parser.add_argument(
            "--match-metres", type=int, default=MATCH_METRES,
            help="How close a survey station must be to one on the map.",
        )
        parser.add_argument(
            "--band-only", action="store_true",
            help="Import the regional median and skip the per-station prices.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        region = options["region"]
        week = self._week(options["week_of"])

        stations, as_of = self._read_survey()

        written = 0
        if not options["band_only"]:
            written = self._import_station_prices(
                stations, as_of,
                tolerance=options["match_metres"],
                dry_run=options["dry_run"],
            )

        grades = self._import_band(
            stations, region=region, week=week, dry_run=options["dry_run"]
        )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{written} station price(s) written, "
            f"{grades} regional grade(s) for {region}, week of {week}."
        ))
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run - nothing was written."))
        else:
            self.stdout.write(
                "A station price outranks the regional median and the brand "
                "advisory. Anything you log yourself still outranks all three."
            )

    # ---------------------------------------------------------------- reading

    def _week(self, raw: str):
        if not raw:
            return week_start()
        try:
            return week_start(datetime.strptime(raw, "%Y-%m-%d").date())
        except ValueError as exc:
            raise CommandError("--week-of must be YYYY-MM-DD") from exc

    def _read_survey(self) -> tuple[list[dict], str]:
        """Station list and current prices, merged into one list."""
        headers = {"User-Agent": "OneApp/1.0 (personal fuel price tracker)"}

        self.stdout.write("Reading the GasWatch station list... ", ending="")
        try:
            response = httpx.get(STATION_URL, timeout=REQUEST_TIMEOUT,
                                 headers=headers, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self.stdout.write(self.style.ERROR("failed"))
            raise CommandError(f"Could not read the station list: {exc}") from exc

        stations, raw_date = parse_stations(response.text)
        self.stdout.write(self.style.SUCCESS(f"{len(stations)} stations"))

        self.stdout.write("Reading the current prices... ", ending="")
        overrides: dict[str, dict] = {}
        try:
            response = httpx.get(PRICES_URL, timeout=REQUEST_TIMEOUT,
                                 headers={**headers, "Accept": "application/json"})
            response.raise_for_status()
            overrides = response.json().get("overrides") or {}
            self.stdout.write(self.style.SUCCESS(f"{len(overrides)} overridden"))
        except (httpx.HTTPError, ValueError) as exc:
            # The baked prices in data.js are still usable, just older. Losing
            # the overrides is worth a warning, not an abandoned run.
            self.stdout.write(self.style.WARNING(f"unavailable ({exc})"))

        for station in stations:
            override = overrides.get(str(station.get("id")))
            if not isinstance(override, dict):
                continue
            prices = station.setdefault("prices", {})
            for grade, info in override.items():
                if isinstance(info, dict) and info.get("p"):
                    prices[grade] = info["p"]

        return stations, raw_date

    # -------------------------------------------------------- station prices

    def _import_station_prices(self, stations, raw_date, *, tolerance, dry_run):
        as_of = survey_date(raw_date)

        ours = list(
            Place.objects.filter(kind=PlaceKind.FUEL)
            .only("pk", "brand", "latitude", "longitude")
        )
        if not ours:
            self.stdout.write(self.style.WARNING(
                "No stations on the map yet, so nothing to attach prices to. "
                "Run import_places first."
            ))
            return 0

        # Bucketed by hundredth of a degree - roughly a kilometre - so each
        # survey station is compared against its own neighbourhood rather than
        # against eighteen thousand places.
        grid: dict[tuple[int, int], list] = {}
        for place in ours:
            key = (int(float(place.latitude) * 100), int(float(place.longitude) * 100))
            grid.setdefault(key, []).append(place)

        matched: dict[int, tuple[dict, float]] = {}
        unmatched = 0
        for station in stations:
            place = self._nearest(station, grid, tolerance)
            if place is None:
                unmatched += 1
                continue

            distance = metres_between(
                station["lat"], station["lng"],
                float(place.latitude), float(place.longitude),
            )
            # Two survey stations can sit inside one forecourt on the map. The
            # nearer one describes it better.
            current = matched.get(place.pk)
            if current is None or distance < current[1]:
                matched[place.pk] = (station, distance)

        self.stdout.write(
            f"Matched {len(matched)} of {len(ours)} stations on the map "
            f"({unmatched} survey stations had none within {tolerance}m)"
        )

        written = 0
        with transaction.atomic():
            for place_id, (station, _distance) in matched.items():
                label = station.get("name", "")
                if station.get("area"):
                    label = f"{label} ({station['area']})".strip()

                for raw_grade, price in (station.get("prices") or {}).items():
                    grade = GRADE_MAP.get(raw_grade)
                    if not grade or not price:
                        continue

                    if not dry_run:
                        StationSurveyPrice.objects.update_or_create(
                            place_id=place_id,
                            fuel_type=grade,
                            defaults={
                                "price": Decimal(str(price)),
                                "as_of": as_of,
                                "source_name": SOURCE_NAME,
                                "source_url": STATION_URL,
                                "station_label": label[:200],
                                "fetched_at": timezone.now(),
                            },
                        )
                    written += 1

            if dry_run:
                transaction.set_rollback(True)

        return written

    def _nearest(self, station, grid, tolerance):
        """The closest station on the map of the same brand, or nothing."""
        try:
            lat, lng = float(station["lat"]), float(station["lng"])
        except (KeyError, TypeError, ValueError):
            return None

        brand = normalise_brand(station.get("brand", ""))
        if not brand:
            return None

        key_lat, key_lng = int(lat * 100), int(lng * 100)
        candidates = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                candidates.extend(grid.get((key_lat + dy, key_lng + dx), []))

        best = None
        for place in candidates:
            if place.brand != brand:
                continue
            distance = metres_between(
                lat, lng, float(place.latitude), float(place.longitude)
            )
            if distance <= tolerance and (best is None or distance < best[0]):
                best = (distance, place)

        return best[1] if best else None

    # -------------------------------------------------------- regional median

    def _import_band(self, stations, *, region, week, dry_run):
        samples: dict[str, list[Decimal]] = {}
        for station in stations:
            for raw_grade, price in (station.get("prices") or {}).items():
                grade = GRADE_MAP.get(raw_grade)
                if grade and price:
                    samples.setdefault(grade, []).append(Decimal(str(price)))

        written = 0
        with transaction.atomic():
            for grade, prices in sorted(samples.items()):
                if len(prices) < MIN_SAMPLE:
                    self.stdout.write(self.style.WARNING(
                        f"  {grade}: only {len(prices)} readings, skipped"
                    ))
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

                if dry_run:
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
                        "source_url": PRICES_URL,
                        "source_note": SOURCE_NAME,
                        "fetched_at": timezone.now(),
                    },
                )
                written += 1

            if dry_run:
                transaction.set_rollback(True)

        return written
