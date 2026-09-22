"""
Fuel module screens.

Everything the map reads is bounded on the server: the browser gets only the
stations inside the box it is currently looking at, never a full station table.
That is partly speed, but mostly that a client-side filter is not a filter - it
is a full copy of the data with some of it hidden.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.tables import Column, build_table
from apps.core.views import module

from apps.places.models import Place, PlaceKind
from apps.places.regions import REGION_NAMES

from .forms import AdvisoryEntryForm, PriceReportForm
from .models import DOEAdvisory, FuelType, PriceObservation, Vehicle
from .services import quotes_for, score_options, week_start


def fuel_places():
    """Places of kind fuel.

    Every place now lives in one table so the map can draw fuel, markets and
    shops together; the fuel module still only ever means the fuel ones, and
    forgetting this filter would quietly offer you a Jollibee to refuel at.
    """
    return Place.objects.filter(kind=PlaceKind.FUEL)


# Metro Manila. The app opens on the whole metro area first.
DEFAULT_CENTER = (14.5995, 120.9842)
DEFAULT_ZOOM = 11


def _fuel_choice(request, default: str = "") -> str:
    """The grade being priced, from the query string or the default vehicle."""
    requested = request.GET.get("fuel", "")
    valid = {choice.value for choice in FuelType}
    if requested in valid:
        return requested
    if default in valid:
        return default
    vehicle = Vehicle.objects.filter(is_default=True).first()
    return vehicle.default_fuel_type if vehicle else FuelType.GAS_95


def price_coverage() -> dict:
    """Why stations do or do not have a price yet.

    Every station showing "No price" is the correct answer to an empty
    database, but on its own it reads as a broken map rather than a one-minute
    task. This gives the screens enough to say which of the two it is.
    """
    week = week_start()
    advisory_rows = DOEAdvisory.objects.filter(week_of=week).count()
    latest = DOEAdvisory.objects.order_by("-week_of").first()

    band = (
        DOEAdvisory.objects.filter(week_of=week, brand="", low__isnull=False)
        .order_by("-sample_size")
        .first()
    )

    return {
        "week": week,
        "band": band,
        "advisory_rows": advisory_rows,
        "latest_advisory": latest,
        "logged_places": (
            PriceObservation.objects.values("place_id").distinct().count()
        ),
        "stations": fuel_places().count(),
        # The distinction that matters: nothing at all, versus something out
        # of date. They need different prompts.
        "has_any_advisory": latest is not None,
    }


def _decimal(raw, fallback: Decimal) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


@login_required
@module("fuel_map", "Station map")
def station_map(request):
    """The map shell.

    Deliberately ships no station data. The pins arrive from stations_json for
    whatever box the map is showing, so panning to Cebu does not mean the
    browser was holding Cebu all along.
    """
    fuel = _fuel_choice(request)
    vehicle = Vehicle.objects.filter(is_default=True).first() or Vehicle.objects.first()

    context = {
        "fuel": fuel,
        "fuel_choices": FuelType.choices,
        "vehicle": vehicle,
        "brands": (
            fuel_places().exclude(brand="")
            .values_list("brand", flat=True)
            .order_by("brand")
            .distinct()
        ),
        "center_lat": DEFAULT_CENTER[0],
        "center_lng": DEFAULT_CENTER[1],
        "zoom": DEFAULT_ZOOM,
        "max_stations": settings.MAP_MAX_STATIONS,
        "tile_url": settings.MAP_TILE_URL,
        "tile_attribution": settings.MAP_TILE_ATTRIBUTION,
        "tile_max_zoom": settings.MAP_TILE_MAX_ZOOM,
        "default_liters": (
            vehicle.tank_capacity_l if vehicle else Decimal("40")
        ),
        "station_count": fuel_places().count(),
        "coverage": price_coverage(),
    }
    return render(request, "fuel/map.html", context)


@login_required
def stations_json(request):
    """Stations inside a bounding box, priced and scored.

    Capped at MAP_MAX_STATIONS. The cap is reported back so the map can say
    "showing 300 of 812 here" rather than quietly drawing a subset and letting
    you believe you are looking at everything.
    """
    try:
        south = float(request.GET["south"])
        west = float(request.GET["west"])
        north = float(request.GET["north"])
        east = float(request.GET["east"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "south, west, north and east are required"},
                            status=400)

    fuel = _fuel_choice(request)
    queryset = fuel_places().filter(
        latitude__gte=south, latitude__lte=north,
        longitude__gte=west, longitude__lte=east,
    )

    brand = request.GET.get("brand", "")
    if brand:
        queryset = queryset.filter(brand=brand)
    if request.GET.get("favorites") == "1":
        queryset = queryset.filter(is_favorite=True)

    total = queryset.count()
    limit = settings.MAP_MAX_STATIONS
    # Favourites first so the cap never hides a station you actually use.
    stations = list(queryset.order_by("-is_favorite", "name")[:limit])

    origin = None
    try:
        origin = (float(request.GET["lat"]), float(request.GET["lng"]))
    except (KeyError, ValueError):
        pass

    vehicle = Vehicle.objects.filter(is_default=True).first()
    liters = _decimal(
        request.GET.get("liters"),
        vehicle.tank_capacity_l if vehicle else Decimal("40"),
    )
    km_per_liter = vehicle.km_per_liter if vehicle else Decimal("10")

    options = score_options(
        stations, fuel, liters=liters, km_per_liter=km_per_liter, origin=origin
    )
    comparable_costs = [
        option.effective_cost for option in options if option.effective_cost is not None
    ]
    best_cost = min(comparable_costs) if comparable_costs else None

    return JsonResponse({
        "total": total,
        "shown": len(options),
        "truncated": total > len(options),
        "fuel": fuel,
        "liters": str(liters),
        "stations": [
            {
                "id": option.place.pk,
                "name": option.place.display_name,
                "brand": option.place.brand,
                "city": option.place.locality,
                "lat": float(option.place.latitude),
                "lng": float(option.place.longitude),
                "favorite": option.place.is_favorite,
                "price": str(option.quote.price) if option.quote.price else None,
                "tier": option.quote.tier,
                "tier_label": option.quote.tier_label,
                "as_of": option.quote.as_of.isoformat() if option.quote.as_of else None,
                "detail": option.quote.detail,
                "distance_km": str(option.distance_km) if option.distance_km else None,
                "effective_cost": (
                    str(option.effective_cost) if option.effective_cost else None
                ),
                "saving": (
                    str(option.saving_vs_worst)
                    if option.saving_vs_worst is not None else None
                ),
                "difference_vs_best": (
                    str(option.effective_cost - best_cost)
                    if option.effective_cost is not None and best_cost is not None
                    else None
                ),
                "url": f"/fuel/stations/{option.place.pk}/",
            }
            for option in options
        ],
    })


@login_required
@module("fuel_map", "Station")
def station_detail(request, pk: int):
    station = get_object_or_404(Place, pk=pk, kind=PlaceKind.FUEL)
    fuel = _fuel_choice(request)

    if request.method == "POST":
        form = PriceReportForm(request.POST, place=station)
        if form.is_valid():
            form.save()
            messages.success(request, "Price noted.")
            return redirect("fuel:station_detail", pk=station.pk)
        messages.error(request, "That price could not be saved.")
    else:
        form = PriceReportForm(place=station)

    history = (
        station.fuel_observations.filter(fuel_type=fuel).order_by("-observed_at")[:30]
    )

    context = {
        "station": station,
        "fuel": fuel,
        "fuel_choices": FuelType.choices,
        "quote": quotes_for([station], fuel)[station.pk],
        "grades": [
            {
                "value": choice.value,
                "label": choice.label,
                "quote": quotes_for([station], choice.value)[station.pk],
            }
            for choice in FuelType
        ],
        "history": history,
        "form": form,
    }
    return render(request, "fuel/station_detail.html", context)


@login_required
@require_POST
def station_favorite(request, pk: int):
    station = get_object_or_404(Place, pk=pk, kind=PlaceKind.FUEL)
    station.is_favorite = not station.is_favorite
    station.save(update_fields=["is_favorite"])
    messages.success(
        request,
        f"{station.display_name} {'pinned' if station.is_favorite else 'unpinned'}.",
    )

    # Only same-site paths are honoured. A "next" that can point anywhere is an
    # open redirect, and it costs one check to not have one.
    next_url = request.POST.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("fuel:station_detail", pk=station.pk)


@login_required
@module("fuel_advisory", "DOE advisory")
def advisory(request):
    if request.method == "POST":
        form = AdvisoryEntryForm(request.POST)
        if form.is_valid():
            written = form.save()
            messages.success(
                request,
                f"{written} price(s) saved for {form.cleaned_data['region']}.",
            )
            return redirect("fuel:advisory")
        messages.error(request, "Check the highlighted fields.")
    else:
        form = AdvisoryEntryForm()

    queryset = DOEAdvisory.objects.all()
    region = request.GET.get("region", "")
    if region:
        queryset = queryset.filter(region=region)

    columns = [
        Column("week_of", "Week of", order_by=("week_of",)),
        Column("region", "Region", order_by=("region",)),
        Column("brand", "Brand", order_by=("brand",)),
        Column("fuel_type", "Grade", order_by=("fuel_type",)),
        Column("price", "Price", order_by=("price",), align="right"),
        Column("source", "Source", order_by=("source_note",)),
    ]
    table = build_table(
        request, queryset, columns,
        default_sort="week_of", default_desc=True, preserve=("region",),
    )

    current_week = week_start()
    context = {
        "form": form,
        "table": table,
        "rows": table.page.object_list,
        "regions": REGION_NAMES,
        "region": region,
        "current_week": current_week,
        "current_week_rows": DOEAdvisory.objects.filter(week_of=current_week).count(),
        "covered_regions": (
            DOEAdvisory.objects.filter(week_of=current_week)
            .values_list("region", flat=True).distinct().count()
        ),
    }
    return render(request, "fuel/advisory.html", context)
