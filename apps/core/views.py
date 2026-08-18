"""The overview screen, and the helper every module view uses to place itself."""

from __future__ import annotations

import functools
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Sum
from django.shortcuts import render
from django.utils import timezone

from apps.fuel.models import DOEAdvisory, FillUp, PriceObservation, Station
from apps.fuel.services import fuel_economy, week_start
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
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start = month_start.replace(month=1)

    this_month = FillUp.objects.filter(filled_at__gte=month_start)
    month_totals = this_month.aggregate(
        spend=Sum("total_cost"), liters=Sum("liters"), avg_price=Avg("price_per_liter")
    )

    previous_month_end = month_start - timedelta(seconds=1)
    previous_month_start = previous_month_end.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    previous_spend = FillUp.objects.filter(
        filled_at__gte=previous_month_start, filled_at__lte=previous_month_end
    ).aggregate(spend=Sum("total_cost"))["spend"]

    year_spend = FillUp.objects.filter(filled_at__gte=year_start).aggregate(
        spend=Sum("total_cost")
    )["spend"]

    # How much of the map actually has a price behind it. Stated plainly on the
    # dashboard because it is the honest caveat on every comparison the app
    # makes: no live feed exists, so coverage is whatever we have gathered.
    station_count = Station.objects.count()
    fresh_cutoff = now - timedelta(days=14)
    priced_stations = (
        PriceObservation.objects.filter(observed_at__gte=fresh_cutoff)
        .values("station_id")
        .distinct()
        .count()
    )
    current_week = week_start()
    advisory_rows = DOEAdvisory.objects.filter(week_of=current_week).count()
    advisory_latest = DOEAdvisory.objects.order_by("-week_of").first()

    recent = (
        FillUp.objects.select_related("station", "vehicle")
        .order_by("-filled_at")[:8]
    )

    spend_change = None
    if previous_spend and month_totals["spend"]:
        spend_change = month_totals["spend"] - previous_spend

    by_brand = (
        FillUp.objects.filter(filled_at__gte=year_start)
        .values("station__brand")
        .annotate(spend=Sum("total_cost"), visits=Count("id"),
                  avg_price=Avg("price_per_liter"))
        .order_by("-spend")[:6]
    )

    context = {
        "month_spend": month_totals["spend"] or Decimal(0),
        "month_liters": month_totals["liters"] or Decimal(0),
        "month_avg_price": month_totals["avg_price"],
        "spend_change": spend_change,
        "year_spend": year_spend or Decimal(0),
        "station_count": station_count,
        "priced_stations": priced_stations,
        "price_coverage": (
            round(100 * priced_stations / station_count) if station_count else 0
        ),
        "advisory_rows": advisory_rows,
        "advisory_week": current_week,
        "advisory_latest": advisory_latest,
        "recent_fill_ups": recent,
        "economy": fuel_economy(FillUp.objects.order_by("-filled_at")[:20]),
        "by_brand": by_brand,
        "has_data": FillUp.objects.exists(),
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
