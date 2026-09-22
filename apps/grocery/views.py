"""Grocery screens: commodity prices and nearby supermarket options."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Min, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.tables import Column, build_table
from apps.core.views import module
from apps.places.geo import road_km
from apps.places.models import Place, PlaceKind
from apps.spend.shopping import known_prices, reference_price

from .chart import build as build_chart
from .models import Commodity, CommodityCategory, CommodityPrice
from .services import LONG_WINDOW, SHORT_WINDOW, biggest_movers, movements

DEFAULT_CENTER = (14.5995, 120.9842)
DEFAULT_ZOOM = 12
MAX_GROCERY_STORES = 160

STORE_GROUPS = {
    "sm": {
        "label": "SM / Hypermarket",
        "brands": ("SM", "SM Supermarket", "SM Hypermarket", "Savemore"),
        "terms": ("SM Supermarket", "SM Hypermarket", "Savemore"),
    },
    "puregold": {
        "label": "Puregold",
        "brands": ("Puregold",),
        "terms": ("Puregold",),
    },
    "robinsons": {
        "label": "Robinsons",
        "brands": ("Robinsons", "Robinsons Supermarket", "Robinsons Easymart"),
        "terms": ("Robinsons Supermarket", "Robinsons Easymart"),
    },
}


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
@module("grocery_map", "Grocery map")
def grocery_map(request):
    """Map shell for nearby supermarkets and item price comparisons."""
    favorite = (
        Place.objects.filter(kind=PlaceKind.SUPERMARKET, is_favorite=True).first()
    )
    center = (
        (float(favorite.latitude), float(favorite.longitude))
        if favorite else DEFAULT_CENTER
    )
    latest_day = CommodityPrice.objects.aggregate(day=Max("observed_on"))["day"]

    return render(request, "grocery/map.html", {
        "center_lat": center[0],
        "center_lng": center[1],
        "zoom": DEFAULT_ZOOM,
        "item": request.GET.get("item", "").strip(),
        "store_count": Place.objects.filter(kind=PlaceKind.SUPERMARKET).count(),
        "latest_day": latest_day,
        "store_groups": [(key, value["label"]) for key, value in STORE_GROUPS.items()],
        "tile_url": settings.MAP_TILE_URL,
        "tile_attribution": settings.MAP_TILE_ATTRIBUTION,
        "tile_max_zoom": settings.MAP_TILE_MAX_ZOOM,
        "max_stores": min(settings.MAP_MAX_STATIONS, MAX_GROCERY_STORES),
    })


def _store_group_filter(group: str) -> Q:
    data = STORE_GROUPS.get(group)
    if not data:
        return Q()

    query = Q(brand__in=data["brands"])
    for term in data["terms"]:
        query |= Q(name__icontains=term)
    return query


@login_required
def stores_json(request):
    """Supermarkets inside a viewport, ranked by known item price when possible."""
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
        kind=PlaceKind.SUPERMARKET,
        latitude__gte=south, latitude__lte=north,
        longitude__gte=west, longitude__lte=east,
    )

    store_group = request.GET.get("store_group", "")
    queryset = queryset.filter(_store_group_filter(store_group))

    search = request.GET.get("store", "").strip()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) | Q(brand__icontains=search)
        )

    origin = None
    try:
        origin = (float(request.GET["lat"]), float(request.GET["lng"]))
    except (KeyError, ValueError):
        pass

    item = request.GET.get("item", "").strip()
    price_map = known_prices(item) if item else {}
    total = queryset.count()
    limit = min(settings.MAP_MAX_STATIONS, MAX_GROCERY_STORES)

    if price_map:
        priced = list(queryset.filter(pk__in=price_map.keys())[:limit])
        remaining = max(limit - len(priced), 0)
        fallback = list(
            queryset.exclude(pk__in=price_map.keys())
            .order_by("-is_favorite", "name")[:remaining]
        )
        stores = priced + fallback
    else:
        stores = list(queryset.order_by("-is_favorite", "name")[:limit])

    store_ids = {store.pk for store in stores}
    prices_in_view = [
        price for store_id, (price, _visits) in price_map.items()
        if store_id in store_ids
    ]
    best_price = min(prices_in_view) if prices_in_view else None
    reference = reference_price(item) if item else None

    distances = {}
    if origin:
        distances = {
            store.pk: road_km(
                origin[0], origin[1], float(store.latitude), float(store.longitude)
            )
            for store in stores
        }

    def sort_key(store: Place):
        price = price_map.get(store.pk, (None, 0))[0]
        distance = distances.get(store.pk, Decimal("999999"))
        if item and best_price is not None:
            return (price is None, price or Decimal("999999"), distance)
        return (not store.is_favorite, distance, store.display_name)

    stores.sort(key=sort_key)

    payload = []
    for store in stores:
        price, visits = price_map.get(store.pk, (None, 0))
        payload.append({
            "id": store.pk,
            "name": store.display_name,
            "brand": store.brand,
            "city": store.locality,
            "lat": float(store.latitude),
            "lng": float(store.longitude),
            "favorite": store.is_favorite,
            "distance_km": str(distances[store.pk]) if store.pk in distances else None,
            "price": str(price) if price is not None else None,
            "visits": visits,
            "difference_vs_best": (
                str(price - best_price)
                if price is not None and best_price is not None else None
            ),
            "url": f"/map/{store.pk}/",
        })

    reference_payload = None
    if reference:
        reference_payload = {
            "commodity": reference["commodity"].label,
            "price": str(reference["price"]),
            "unit": reference["unit"],
            "observed_on": reference["observed_on"].isoformat(),
        }

    return JsonResponse({
        "total": total,
        "shown": len(payload),
        "truncated": total > len(payload),
        "sorted_by": (
            "known_price" if item and best_price is not None
            else "distance" if origin else "name"
        ),
        "item": item,
        "reference": reference_payload,
        "stores": payload,
    })


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
