"""The overview screen, and the helper every module view uses to place itself."""

from __future__ import annotations

import functools
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from apps.fuel.models import DOEAdvisory, PriceObservation
from apps.fuel.services import week_start
from apps.places.models import Place, PlaceKind
from apps.grocery.models import CommodityPrice
from apps.grocery.services import biggest_movers


def module(code: str, title: str):
    """Mark a view as belonging to a sidebar module.

    Stamps the request so the sidebar highlights the right row, and supplies
    the page title, which the topbar reads. Doing it here means a new screen
    cannot forget one and silently render with no active nav row.
    """

    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            request.current_module = code
            request.page_title = title
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


@login_required
@module("home", "Overview")
def home(request):
    now = timezone.now()

    # How much of the map actually has a price behind it. Stated plainly on the
    # dashboard because it is the honest caveat on every comparison the app
    # makes: no live feed exists, so coverage is whatever we have gathered.
    station_count = Place.objects.filter(kind=PlaceKind.FUEL).count()
    fresh_cutoff = now - timedelta(days=14)
    priced_stations = (
        PriceObservation.objects.filter(observed_at__gte=fresh_cutoff)
        .values("place_id")
        .distinct()
        .count()
    )
    current_week = week_start()
    advisory_rows = DOEAdvisory.objects.filter(week_of=current_week).count()
    advisory_latest = DOEAdvisory.objects.order_by("-week_of").first()

    context = {
        "station_count": station_count,
        "priced_stations": priced_stations,
        "price_coverage": (
            round(100 * priced_stations / station_count) if station_count else 0
        ),
        "advisory_rows": advisory_rows,
        "advisory_week": current_week,
        "advisory_latest": advisory_latest,
        # Grocery is the one module with an official daily feed, so the
        # overview can say something useful about it on day one - before a
        # single receipt has been logged.
        "movers": biggest_movers(limit=6),
        "grocery_points": CommodityPrice.objects.count(),
        "grocery_latest": CommodityPrice.objects.order_by("-observed_on")
                                                .values_list("observed_on", flat=True)
                                                .first(),
    }
    return render(request, "core/home.html", context)


def offline(request):
    """Shown when a page is requested with no connection.

    Deliberately reachable while signed out: the service worker caches it at
    install time, before anyone has necessarily signed in.
    """
    template = "core/offline.html" if request.user.is_authenticated else "core/offline_shell.html"
    return render(request, template, {"page_title": "Offline"})
