"""Spend screens: purchases, promos and the wardrobe."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.cards.services import rank_cards
from apps.core.categories import SpendCategory
from apps.core.tables import Column, build_table
from apps.core.views import module

from .forms import ItemForm, PromoForm, PurchaseForm
from .models import Promo, Purchase, PurchaseItem
from .services import (
    expiring_promos,
    live_promos,
    spend_by_category,
    stale_promos,
    wardrobe,
    wardrobe_summary,
)


@login_required
@module("spend_purchases", "Spending")
def purchases(request):
    queryset = Purchase.objects.select_related("place", "card")

    category = request.GET.get("category", "")
    if category in SpendCategory.values:
        queryset = queryset.filter(category=category)

    columns = [
        Column("occurred_at", "When", order_by=("occurred_at",)),
        Column("where", "Where", order_by=("place__name", "merchant")),
        Column("category", "Category", order_by=("category",)),
        Column("card", "Paid with", order_by=("card__name",)),
        Column("total", "Total", order_by=("total",), align="right"),
        Column("actions", "", align="right"),
    ]
    table = build_table(
        request, queryset, columns,
        default_sort="occurred_at", default_desc=True, preserve=("category",),
    )

    return render(request, "spend/purchases.html", {
        "table": table,
        "purchases": table.page.object_list,
        "categories": SpendCategory.choices,
        "category": category,
        "totals": queryset.aggregate(spent=Sum("total")),
        "by_category": spend_by_category(),
    })


@login_required
@module("spend_purchases", "Log a purchase")
def purchase_create(request):
    if request.method == "POST":
        form = PurchaseForm(request.POST)
        if form.is_valid():
            purchase = form.save()
            messages.success(request, "Purchase logged.")
            return redirect("spend:purchase_detail", pk=purchase.pk)
        messages.error(request, "Check the highlighted fields.")
    else:
        initial = {}
        place_id = request.GET.get("place", "")
        if place_id.isdigit():
            initial["place"] = place_id
        form = PurchaseForm(initial=initial or None)

    return render(request, "spend/purchase_form.html",
                  {"form": form, "creating": True})


@login_required
@module("spend_purchases", "Purchase")
def purchase_detail(request, pk: int):
    purchase = get_object_or_404(
        Purchase.objects.select_related("place", "card").prefetch_related("items"),
        pk=pk,
    )
    request.page_title = purchase.where

    if request.method == "POST":
        form = ItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.purchase = purchase
            item.save()
            messages.success(request, "Line added.")
            return redirect("spend:purchase_detail", pk=purchase.pk)
        messages.error(request, "Check the highlighted fields.")
    else:
        form = ItemForm()

    # What the card actually earned, so a logged purchase can be checked
    # against the advice the app would have given.
    picks = rank_cards(
        category=purchase.category,
        brand=purchase.place.brand if purchase.place else "",
        amount=purchase.total,
    )
    used = next(
        (p for p in picks if purchase.card_id and p.card.pk == purchase.card_id),
        None,
    )

    return render(request, "spend/purchase_detail.html", {
        "purchase": purchase,
        "form": form,
        "best_pick": picks[0] if picks and picks[0].value > 0 else None,
        "used_pick": used,
    })


@login_required
@require_POST
def purchase_delete(request, pk: int):
    purchase = get_object_or_404(Purchase, pk=pk)
    purchase.delete()
    messages.success(request, "Purchase deleted.")
    return redirect("spend:purchases")


@login_required
@require_POST
def item_wear(request, pk: int):
    """Record one more wearing. The whole cost-per-wear loop is this button."""
    item = get_object_or_404(PurchaseItem, pk=pk)
    item.wears += 1
    item.save(update_fields=["wears"])

    next_url = request.POST.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("spend:wardrobe")


@login_required
@module("spend_wardrobe", "Wardrobe")
def wardrobe_screen(request):
    return render(request, "spend/wardrobe.html", {
        "items": wardrobe(),
        "summary": wardrobe_summary(),
    })


@login_required
@module("spend_promos", "Promos")
def promos(request):
    category = request.GET.get("category", "")
    if category not in SpendCategory.values:
        category = ""

    if request.method == "POST":
        form = PromoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Promo saved.")
            return redirect("spend:promos")
        messages.error(request, "Check the highlighted fields.")
    else:
        form = PromoForm()

    live = live_promos(category=category)

    return render(request, "spend/promos.html", {
        "form": form,
        "live": live,
        "expiring": expiring_promos(),
        "stale": stale_promos(),
        "expired": [p for p in Promo.objects.all() if not p.is_live][:20],
        "categories": SpendCategory.choices,
        "category": category,
        "undated": [p for p in live if p.undated],
    })


@login_required
@require_POST
def promo_delete(request, pk: int):
    promo = get_object_or_404(Promo, pk=pk)
    promo.delete()
    messages.success(request, "Promo removed.")
    return redirect("spend:promos")
