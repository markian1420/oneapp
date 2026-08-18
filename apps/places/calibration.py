"""
What the app actually knows about where you are standing.

Everything here is regional, and the app was built in Metro Manila. Drive home
to the province and three separate things quietly stop being true:

  * the map has no places there, so "nearest" has nothing to rank;
  * the fuel baseline is a DOE advisory for the wrong region;
  * the grocery benchmark is the DA index, which is published for NCR only.

None of those fail loudly. The map just looks empty and the prices just look
like prices. So rather than let the app drift out of calibration in silence,
this reports each one and says exactly what would fix it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Count

from apps.places.locate import Fix, reverse_geocode
from apps.places.models import Place
from apps.places.regions import NCR

# The DA publishes its Daily Price Index for the National Capital Region.
# Regional offices issue their own bulletins in their own formats, none of
# which this app parses - so outside NCR the commodity screen is a reference
# for somewhere else.
GROCERY_INDEX_REGIONS = {NCR}


@dataclass
class Gap:
    """Something that does not apply here, and what would fix it."""

    key: str
    title: str
    detail: str
    fix: str = ""
    severity: str = "warn"      # warn | info

    @property
    def badge_class(self) -> str:
        return "badge-warning" if self.severity == "warn" else "badge-info"


@dataclass
class Calibration:
    """Where you are, and how much of the app is valid there."""

    fix: Fix
    places: int = 0
    place_kinds: int = 0
    fuel_advisory_rows: int = 0
    gaps: list[Gap] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.fix.resolved

    @property
    def is_calibrated(self) -> bool:
        return self.resolved and not self.gaps

    @property
    def headline(self) -> str:
        if not self.resolved:
            return self.fix.error or "Could not work out where you are"
        if self.is_calibrated:
            return f"Calibrated for {self.fix.label}"
        return f"{len(self.gaps)} thing(s) do not apply in {self.fix.label}"


def calibrate(latitude: float, longitude: float) -> Calibration:
    """Resolve a location and check what the app can honestly offer there."""
    fix = reverse_geocode(latitude, longitude)
    state = Calibration(fix=fix)
    if not fix.resolved:
        return state

    here = Place.objects.filter(region=fix.region)
    state.places = here.count()
    state.place_kinds = here.values("kind").distinct().count()

    # ---- places ---------------------------------------------------------
    if not state.places:
        province = fix.province or fix.region_name
        state.gaps.append(Gap(
            key="places",
            title=f"No places imported for {fix.region_name}",
            detail=(
                "The map, the nearest-to-me ranking and where-to-buy all read "
                "from the same table, and it is empty for this region. They "
                "will show nothing rather than something wrong."
            ),
            fix=f'python manage.py import_places --area "{province}" --kind all',
        ))
    elif state.place_kinds < 3:
        state.gaps.append(Gap(
            key="place_kinds",
            title=f"Only {state.place_kinds} kind(s) of place imported here",
            detail=(
                "Some categories will look empty simply because they were "
                "never imported for this region, not because there is nothing."
            ),
            fix=(
                f'python manage.py import_places '
                f'--area "{fix.province or fix.region_name}" --kind all'
            ),
            severity="info",
        ))

    # ---- fuel -----------------------------------------------------------
    from apps.fuel.models import DOEAdvisory
    from apps.fuel.services import week_start

    state.fuel_advisory_rows = DOEAdvisory.objects.filter(
        region=fix.region, week_of=week_start()
    ).count()
    if not state.fuel_advisory_rows:
        has_any = DOEAdvisory.objects.filter(region=fix.region).exists()
        state.gaps.append(Gap(
            key="fuel",
            title=f"No fuel advisory for {fix.region_name} this week",
            detail=(
                "Prices are resolved per region, so an advisory entered for "
                "Metro Manila says nothing about pumps here."
                + ("" if has_any else " Nothing has ever been entered for this region.")
            ),
            fix="Enter it under Fuel, DOE advisory - pick this region.",
        ))

    # ---- grocery --------------------------------------------------------
    if fix.region not in GROCERY_INDEX_REGIONS:
        state.gaps.append(Gap(
            key="grocery",
            title="The commodity index does not cover this region",
            detail=(
                "The DA publishes its Daily Price Index for NCR only. The "
                "prices on the commodity screen are Metro Manila wet market "
                "rates, which is the wrong benchmark here - regional offices "
                "publish their own bulletins and this app does not read them."
            ),
            fix="Treat commodity prices as NCR-only while you are here.",
        ))

    return state


def coverage_by_region() -> list[dict]:
    """Which regions have places at all, for the calibration screen."""
    from apps.places.regions import REGION_NAMES

    counts = dict(
        Place.objects.exclude(region="")
        .values_list("region")
        .annotate(n=Count("id"))
        .values_list("region", "n")
    )
    return [
        {"code": code, "name": name, "places": counts.get(code, 0)}
        for code, name in REGION_NAMES.items()
    ]
