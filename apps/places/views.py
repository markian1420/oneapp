"""Place detail and regional calibration screens."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.views import module

from .calibration import calibrate, coverage_by_region
from .models import Place, PlaceKind


@login_required
@module("grocery_map", "Place")
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


@login_required
@module("places_calibration", "Where am I")
def calibration(request):
    """What the app knows about wherever you currently are.

    Built for the case of travelling: the app was calibrated in Metro Manila,
    and almost everything in it is regional. Rather than let the map quietly
    turn up empty in the province, this says which parts apply here and what
    would fix the rest.
    """
    state = None
    try:
        latitude = float(request.GET["lat"])
        longitude = float(request.GET["lng"])
    except (KeyError, ValueError):
        latitude = longitude = None

    if latitude is not None:
        state = calibrate(latitude, longitude)

    return render(request, "places/calibration.html", {
        "state": state,
        "coverage": [r for r in coverage_by_region() if r["places"]],
        "empty_regions": [r for r in coverage_by_region() if not r["places"]],
    })
