"""Grocery screens: what commodities cost, and which way they are going."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.tables import Column, build_table
from apps.core.views import module

from .chart import build as build_chart
from .models import Commodity, CommodityCategory, CommodityPrice
from .services import LONG_WINDOW, SHORT_WINDOW, biggest_movers, movements


@login_required
@module("grocery_prices", "Commodity prices")
def commodities(request):
    search = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")

    queryset = Commodity.objects.all()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) | Q(specification__icontains=search)
        )
    if category:
        queryset = queryset.filter(category=category)
    if request.GET.get("tracked") == "1":
        queryset = queryset.filter(is_tracked=True)

    columns = [
        Column("name", "Commodity", order_by=("name", "specification")),
        Column("category", "Category", order_by=("category",)),
        Column("latest", "Latest", align="right",
               note="Resolved from the price series, so not sortable in SQL."),
        Column("week", "7 days", align="right",
               note="Change against the nearest reading a week earlier."),
        Column("month", "30 days", align="right",
               note="Change against the nearest reading a month earlier."),
        Column("points", "Readings", order_by=("reading_count",), align="right"),
        Column("actions", "", align="right"),
    ]

    table = build_table(
        request,
        queryset.annotate(reading_count=Count("prices")),
        columns,
        default_sort="name",
        preserve=("q", "category", "tracked"),
    )

    page_items = list(table.page.object_list)
    # One query for the whole page's series rather than one per row.
    changes = movements(page_items)
    for commodity in page_items:
        commodity.movement = changes[commodity.pk]

    latest_day = CommodityPrice.objects.aggregate(day=Max("observed_on"))["day"]

    context = {
        "table": table,
        "commodities": page_items,
        "q": search,
        "category": category,
        "categories": CommodityCategory.choices,
        "tracked_only": request.GET.get("tracked") == "1",
        "latest_day": latest_day,
        "total_commodities": Commodity.objects.count(),
        "total_points": CommodityPrice.objects.count(),
        "short_window": SHORT_WINDOW,
        "long_window": LONG_WINDOW,
    }

    if request.headers.get("HX-Request"):
        return render(request, "grocery/_commodity_results.html", context)
    return render(request, "grocery/commodities.html", context)


@login_required
@module("grocery_prices", "Commodity")
def commodity_detail(request, pk: int):
    commodity = get_object_or_404(Commodity, pk=pk)
    # The decorator supplies a static title for the sidebar's benefit; the
    # topbar should say which commodity you are actually looking at.
    request.page_title = commodity.name

    series = list(
        commodity.prices.order_by("-observed_on")[:90]
    )
    # Newest first for the table, so each row's delta is against the row below
    # it. Done here because a template cannot look ahead in a loop.
    for newer, older in zip(series, series[1:]):
        newer.delta = newer.price - older.price
    if series:
        series[-1].delta = None

    movement = movements([commodity])[commodity.pk]
    span = commodity.prices.aggregate(
        low=Min("price"), high=Max("price"),
        first=Min("observed_on"), last=Max("observed_on"),
    )

    context = {
        "commodity": commodity,
        "movement": movement,
        "series": series,
        "span": span,
        # Geometry, not data: the SVG is built here so the browser receives a
        # polyline rather than the price table, which is rendered separately
        # as the accessible view of the same numbers.
        "chart": build_chart(reversed(series)),
        "short_window": SHORT_WINDOW,
        "long_window": LONG_WINDOW,
    }
    return render(request, "grocery/commodity_detail.html", context)


@login_required
@require_POST
def commodity_track(request, pk: int):
    commodity = get_object_or_404(Commodity, pk=pk)
    commodity.is_tracked = not commodity.is_tracked
    commodity.save(update_fields=["is_tracked"])

    next_url = request.POST.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("grocery:commodity_detail", pk=commodity.pk)


def movers_panel():
    """Shared with the overview screen."""
    return biggest_movers(limit=6)
