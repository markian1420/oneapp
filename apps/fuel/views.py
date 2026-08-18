"""
Fuel module screens.

Everything that reads a list does it server-side: the browser gets one page of
rows, or the stations inside the box it is currently looking at, and never the
table. That is partly speed, but mostly that a client-side filter is not a
filter - it is a full copy of the data with some of it hidden.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.tables import Column, build_table
from apps.core.views import module

from .forms import AdvisoryEntryForm, FillUpForm, PriceReportForm, VehicleForm
from .models import DOEAdvisory, FillUp, FuelType, Station, Vehicle
from .regions import REGION_NAMES
from .services import fuel_economy, quotes_for, score_options, week_start

# Metro Manila. Only used the first time, before there is a fill-up to centre on.
DEFAULT_CENTER = (14.5995, 120.9842)
DEFAULT_ZOOM = 12


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

    last_fill_up = (
        FillUp.objects.select_related("station").order_by("-filled_at").first()
    )
    if last_fill_up:
        center = (float(last_fill_up.station.latitude), float(last_fill_up.station.longitude))
    else:
        center = DEFAULT_CENTER

    context = {
        "fuel": fuel,
        "fuel_choices": FuelType.choices,
        "vehicle": vehicle,
        "brands": (
            Station.objects.exclude(brand="")
            .values_list("brand", flat=True)
            .order_by("brand")
            .distinct()
        ),
        "center_lat": center[0],
        "center_lng": center[1],
        "zoom": DEFAULT_ZOOM,
        "max_stations": settings.MAP_MAX_STATIONS,
        "default_liters": (
            vehicle.tank_capacity_l if vehicle else Decimal("40")
        ),
        "station_count": Station.objects.count(),
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
    queryset = Station.objects.filter(
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

    return JsonResponse({
        "total": total,
        "shown": len(options),
        "truncated": total > len(options),
        "fuel": fuel,
        "liters": str(liters),
        "stations": [
            {
                "id": option.station.pk,
                "name": option.station.display_name,
                "brand": option.station.brand,
                "city": option.station.locality,
                "lat": float(option.station.latitude),
                "lng": float(option.station.longitude),
                "favorite": option.station.is_favorite,
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
                "url": f"/fuel/stations/{option.station.pk}/",
            }
            for option in options
        ],
    })


@login_required
@module("fuel_stations", "Stations")
def stations(request):
    fuel = _fuel_choice(request)
    search = request.GET.get("q", "").strip()
    brand = request.GET.get("brand", "")
    region = request.GET.get("region", "")

    queryset = Station.objects.all()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(brand__icontains=search)
            | Q(city__icontains=search)
            | Q(street__icontains=search)
        )
    if brand:
        queryset = queryset.filter(brand=brand)
    if region:
        queryset = queryset.filter(region=region)
    if request.GET.get("favorites") == "1":
        queryset = queryset.filter(is_favorite=True)

    columns = [
        Column("name", "Station", order_by=("name",)),
        Column("brand", "Brand", order_by=("brand",)),
        Column("city", "Location", order_by=("city", "province")),
        Column("region", "Region", order_by=("region",)),
        Column("price", "Price", align="right",
               note="Resolved from your logs and the DOE advisory, so not sortable in SQL."),
        Column("fills", "Fill-ups", order_by=("fill_up_count",), align="right"),
    ]

    table = build_table(
        request,
        queryset.annotate(fill_up_count=Count("fill_ups")),
        columns,
        default_sort="name",
        preserve=("q", "brand", "region", "favorites", "fuel"),
    )
    page_stations = list(table.page.object_list)

    # Priced one page at a time: the resolver runs two queries for the rows on
    # screen rather than for every station in the country. The quote is hung on
    # each station because a Django template cannot look a dict up by a
    # variable key without a custom filter, and one attribute is plainer than
    # a filter that exists to work around the template language.
    quotes = quotes_for(page_stations, fuel)
    for station in page_stations:
        station.quote = quotes[station.pk]

    context = {
        "table": table,
        "stations": page_stations,
        "q": search,
        "fuel": fuel,
        "fuel_choices": FuelType.choices,
        "brand": brand,
        "region": region,
        "regions": REGION_NAMES,
        "brands": (
            Station.objects.exclude(brand="")
            .values_list("brand", flat=True).order_by("brand").distinct()
        ),
        "favorites_only": request.GET.get("favorites") == "1",
    }

    # htmx asks for the rows alone; a browser, a bookmark or a crawler asks for
    # the page. Same view, same queryset, so the two can never disagree about
    # what the filters mean.
    if request.headers.get("HX-Request"):
        return render(request, "fuel/_station_results.html", context)
    return render(request, "fuel/stations.html", context)


@login_required
@module("fuel_stations", "Station")
def station_detail(request, pk: int):
    station = get_object_or_404(Station, pk=pk)
    fuel = _fuel_choice(request)

    if request.method == "POST":
        form = PriceReportForm(request.POST, station=station)
        if form.is_valid():
            form.save()
            messages.success(request, "Price noted.")
            return redirect("fuel:station_detail", pk=station.pk)
        messages.error(request, "That price could not be saved.")
    else:
        form = PriceReportForm(station=station)

    history = (
        station.observations.filter(fuel_type=fuel).order_by("-observed_at")[:30]
    )
    fill_ups = station.fill_ups.select_related("vehicle").order_by("-filled_at")[:10]
    spend = station.fill_ups.aggregate(
        total=Sum("total_cost"), visits=Count("id"),
        cheapest=Min("price_per_liter"), dearest=Max("price_per_liter"),
        average=Avg("price_per_liter"),
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
        "fill_ups": fill_ups,
        "spend": spend,
        "form": form,
    }
    return render(request, "fuel/station_detail.html", context)


@login_required
@require_POST
def station_favorite(request, pk: int):
    station = get_object_or_404(Station, pk=pk)
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
@module("fuel_fillups", "Fill-ups")
def fill_ups(request):
    queryset = FillUp.objects.select_related("station", "vehicle")

    vehicle = request.GET.get("vehicle", "")
    if vehicle.isdigit():
        queryset = queryset.filter(vehicle_id=int(vehicle))

    columns = [
        Column("filled_at", "When", order_by=("filled_at",)),
        Column("station", "Station", order_by=("station__name",)),
        Column("fuel_type", "Grade", order_by=("fuel_type",)),
        Column("liters", "Litres", order_by=("liters",), align="right"),
        Column("price", "Per litre", order_by=("price_per_liter",), align="right"),
        Column("total", "Total", order_by=("total_cost",), align="right"),
        Column("odometer", "Odometer", order_by=("odometer_km",), align="right"),
        Column("actions", "", align="right"),
    ]
    table = build_table(
        request, queryset, columns,
        default_sort="filled_at", default_desc=True, preserve=("vehicle",),
    )

    totals = queryset.aggregate(
        spend=Sum("total_cost"), liters=Sum("liters"), average=Avg("price_per_liter")
    )

    context = {
        "table": table,
        "fill_ups": table.page.object_list,
        "totals": totals,
        "vehicles": Vehicle.objects.all(),
        "vehicle_filter": vehicle,
        "economy": fuel_economy(list(queryset.order_by("-filled_at")[:20])),
    }
    return render(request, "fuel/fill_ups.html", context)


@login_required
@module("fuel_fillups", "Log a fill-up")
def fill_up_create(request):
    if not Vehicle.objects.exists():
        messages.warning(request, "Add a vehicle first - a fill-up belongs to one.")
        return redirect("fuel:vehicle_create")
    if not Station.objects.exists():
        messages.warning(
            request,
            "No stations imported yet. Run: python manage.py import_stations --area NCR",
        )
        return redirect("fuel:stations")

    initial_station = request.GET.get("station")
    if request.method == "POST":
        form = FillUpForm(request.POST)
        if form.is_valid():
            fill_up = form.save()
            messages.success(
                request,
                f"Logged {fill_up.liters} L at {fill_up.price_per_liter}/L. "
                "That price is now on the map.",
            )
            return redirect("fuel:fillups")
        messages.error(request, "Check the highlighted fields.")
    else:
        form = FillUpForm(
            initial={"station": initial_station} if initial_station else None
        )

    return render(
        request,
        "fuel/fill_up_form.html",
        {"form": form, "creating": True, "fresh_days": settings.PRICE_FRESH_DAYS},
    )


@login_required
@module("fuel_fillups", "Edit fill-up")
def fill_up_edit(request, pk: int):
    fill_up = get_object_or_404(FillUp, pk=pk)
    if request.method == "POST":
        form = FillUpForm(request.POST, instance=fill_up)
        if form.is_valid():
            form.save()
            messages.success(request, "Fill-up updated.")
            return redirect("fuel:fillups")
        messages.error(request, "Check the highlighted fields.")
    else:
        form = FillUpForm(instance=fill_up)

    return render(
        request,
        "fuel/fill_up_form.html",
        {
            "form": form,
            "creating": False,
            "fill_up": fill_up,
            "fresh_days": settings.PRICE_FRESH_DAYS,
        },
    )


@login_required
@require_POST
def fill_up_delete(request, pk: int):
    fill_up = get_object_or_404(FillUp, pk=pk)
    observation = fill_up.observation
    fill_up.delete()
    # The price came from this receipt, so it goes with it rather than
    # lingering as a number with nothing behind it.
    if observation:
        observation.delete()
    messages.success(request, "Fill-up deleted.")
    return redirect("fuel:fillups")


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


@login_required
@module("fuel_vehicles", "Vehicles")
def vehicles(request):
    context = {
        "vehicles": Vehicle.objects.annotate(
            fill_up_count=Count("fill_ups"), spend=Sum("fill_ups__total_cost")
        ),
        "economy_by_vehicle": {
            vehicle.pk: fuel_economy(list(vehicle.fill_ups.all()))
            for vehicle in Vehicle.objects.prefetch_related("fill_ups")
        },
    }
    return render(request, "fuel/vehicles.html", context)


@login_required
@module("fuel_vehicles", "Add vehicle")
def vehicle_create(request):
    if request.method == "POST":
        form = VehicleForm(request.POST)
        if form.is_valid():
            # The first vehicle is the default one; there is nothing to choose
            # between yet and an app with no default vehicle prices nothing.
            vehicle = form.save(commit=False)
            if not Vehicle.objects.exists():
                vehicle.is_default = True
            vehicle.save()
            messages.success(request, f"{vehicle.name} added.")
            return redirect("fuel:vehicles")
        messages.error(request, "Check the highlighted fields.")
    else:
        form = VehicleForm()
    return render(request, "fuel/vehicle_form.html", {"form": form, "creating": True})


@login_required
@module("fuel_vehicles", "Edit vehicle")
def vehicle_edit(request, pk: int):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    if request.method == "POST":
        form = VehicleForm(request.POST, instance=vehicle)
        if form.is_valid():
            form.save()
            messages.success(request, f"{vehicle.name} updated.")
            return redirect("fuel:vehicles")
        messages.error(request, "Check the highlighted fields.")
    else:
        form = VehicleForm(instance=vehicle)
    return render(
        request, "fuel/vehicle_form.html",
        {"form": form, "creating": False, "vehicle": vehicle},
    )
