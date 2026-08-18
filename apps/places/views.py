"""
The map that shows everything.

One bounding-box query over one table returns fuel, markets, shops and
pharmacies together, which is the whole reason places live in a single model.
Asking "what is around me" should not mean one request per kind, and it should
be possible to rank a Puregold against a wet market on the same screen.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.views import module
from apps.fuel.models import FuelType, Vehicle
from apps.fuel.services import quotes_for

from .models import KIND_STYLE, Place, PlaceKind

# Metro Manila, used only before there is anything to centre on.
DEFAULT_CENTER = (14.5995, 120.9842)
DEFAULT_ZOOM = 13


@login_required
@module("places_map", "Map")
def place_map(request):
    """The map shell. Ships no places - the pins arrive per viewport."""
    counts = dict(
        Place.objects.values_list("kind").annotate(n=Count("id")).values_list("kind", "n")
    )

    kinds = [
        {
            "value": kind.value,
            "label": kind.label,
            "count": counts.get(kind.value, 0),
            "tone": KIND_STYLE[kind]["tone"],
            "icon": KIND_STYLE[kind]["icon"],
        }
        for kind in PlaceKind
    ]

    favorite = Place.objects.filter(is_favorite=True).first()
    center = (
        (float(favorite.latitude), float(favorite.longitude))
        if favorite else DEFAULT_CENTER
    )

    vehicle = Vehicle.objects.filter(is_default=True).first() or Vehicle.objects.first()

    return render(request, "places/map.html", {
        "kinds": kinds,
        "total": sum(counts.values()),
        "center_lat": center[0],
        "center_lng": center[1],
        "zoom": DEFAULT_ZOOM,
        "max_places": settings.MAP_MAX_STATIONS,
        "fuel_choices": FuelType.choices,
        "vehicle": vehicle,
    })


@login_required
def places_json(request):
    """Places inside a bounding box, optionally filtered by kind.

    Fuel places carry a price because the fuel module knows how to resolve one.
    Nothing else does yet, and inventing a number for a supermarket would be
    exactly the dishonesty the rest of the app avoids.
    """
    try:
        south = float(request.GET["south"])
        west = float(request.GET["west"])
        north = float(request.GET["north"])
        east = float(request.GET["east"])
    except (KeyError, ValueError):
        return JsonResponse(
            {"error": "south, west, north and east are required"}, status=400
        )

    queryset = Place.objects.filter(
        latitude__gte=south, latitude__lte=north,
        longitude__gte=west, longitude__lte=east,
    )

    wanted = [k for k in request.GET.getlist("kind") if k in PlaceKind.values]
    if wanted:
        queryset = queryset.filter(kind__in=wanted)
    if request.GET.get("favorites") == "1":
        queryset = queryset.filter(is_favorite=True)

    search = request.GET.get("q", "").strip()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) | Q(brand__icontains=search)
        )

    total = queryset.count()
    limit = settings.MAP_MAX_STATIONS
    # Favourites first so the cap never hides somewhere you actually use.
    places = list(queryset.order_by("-is_favorite", "name")[:limit])

    fuel_type = request.GET.get("fuel", "")
    if fuel_type not in FuelType.values:
        vehicle = Vehicle.objects.filter(is_default=True).first()
        fuel_type = vehicle.default_fuel_type if vehicle else FuelType.GAS_95

    # Prices resolve only for fuel, in one batched call for the whole viewport.
    fuel_places = [p for p in places if p.kind == PlaceKind.FUEL]
    quotes = quotes_for(fuel_places, fuel_type) if fuel_places else {}

    payload = []
    for place in places:
        quote = quotes.get(place.pk)
        style = place.style
        payload.append({
            "id": place.pk,
            "kind": place.kind,
            "kind_label": place.get_kind_display(),
            "tone": style["tone"],
            "icon": style["icon"],
            "name": place.display_name,
            "brand": place.brand,
            "city": place.locality,
            "lat": float(place.latitude),
            "lng": float(place.longitude),
            "favorite": place.is_favorite,
            "hours": place.opening_hours,
            "price": str(quote.price) if quote and quote.price else None,
            "tier": quote.tier if quote else None,
            "tier_label": quote.tier_label if quote else None,
            "url": (
                f"/fuel/stations/{place.pk}/" if place.kind == PlaceKind.FUEL
                else f"/places/{place.pk}/"
            ),
        })

    return JsonResponse({
        "total": total,
        "shown": len(payload),
        "truncated": total > len(payload),
        "places": payload,
    })


@login_required
@module("places_map", "Place")
def place_detail(request, pk: int):
    place = get_object_or_404(Place, pk=pk)
    if place.kind == PlaceKind.FUEL:
        # Fuel has a far richer screen of its own.
        return redirect("fuel:station_detail", pk=place.pk)

    request.page_title = place.display_name
    return render(request, "places/detail.html", {"place": place})


@login_required
@require_POST
def place_favorite(request, pk: int):
    place = get_object_or_404(Place, pk=pk)
    place.is_favorite = not place.is_favorite
    place.save(update_fields=["is_favorite"])
    messages.success(
        request,
        f"{place.display_name} {'pinned' if place.is_favorite else 'unpinned'}.",
    )

    next_url = request.POST.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("places:detail", pk=place.pk)
