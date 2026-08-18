"""Card screens: what is in the wallet, and which one to tap."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.categories import SpendCategory, category_for_place
from apps.core.views import module
from apps.places.models import Place

from .forms import CardForm, RewardForm
from .models import Card, Reward
from .services import (
    DEFAULT_BASKET,
    coverage_gaps,
    expiring_rewards,
    rank_cards,
    wallet_summary,
)


@login_required
@module("cards_which", "Which card?")
def which_card(request):
    """The counter question, answered.

    Everything comes from the query string so the answer is a link: you can
    pin "which card at Puregold" to a home screen and it opens already
    answered, which is the difference between using this and not bothering.
    """
    category = request.GET.get("category", "")
    if category not in SpendCategory.values:
        category = ""

    brand = request.GET.get("brand", "").strip()
    place = None

    place_id = request.GET.get("place", "")
    if place_id.isdigit():
        place = Place.objects.filter(pk=int(place_id)).first()
        if place:
            # A place answers both questions at once, which is the whole point
            # of arriving here from the map.
            category = category_for_place(place.kind)
            brand = place.brand

    try:
        amount = Decimal(request.GET.get("amount", "") or DEFAULT_BASKET)
    except (InvalidOperation, ValueError):
        amount = DEFAULT_BASKET
    if amount <= 0:
        amount = DEFAULT_BASKET

    picks = rank_cards(category=category or SpendCategory.OTHER,
                       brand=brand, amount=amount)

    winner = picks[0] if picks and picks[0].value > 0 else None
    runner_up = next(
        (p for p in picks[1:] if p.value > 0), None
    ) if winner else None

    context = {
        "picks": picks,
        "winner": winner,
        "runner_up": runner_up,
        "gap_vs_runner_up": (
            winner.value - runner_up.value if winner and runner_up else None
        ),
        "category": category,
        "categories": SpendCategory.choices,
        "brand": brand,
        "amount": amount,
        "place": place,
        "brands": (
            Place.objects.exclude(brand="")
            .values_list("brand", flat=True).order_by("brand").distinct()[:200]
        ),
        "has_cards": Card.objects.filter(is_active=True).exists(),
    }
    return render(request, "cards/which.html", context)


@login_required
@module("cards_wallet", "My wallet")
def wallet(request):
    cards = (
        Card.objects.prefetch_related("rewards").order_by("-is_active", "name")
    )
    for card in cards:
        card.current_rewards = [r for r in card.rewards.all() if r.is_current]
        card.lapsed_rewards = [r for r in card.rewards.all() if not r.is_current]

    return render(request, "cards/wallet.html", {
        "cards": cards,
        "summary": wallet_summary(),
        "gaps": coverage_gaps(),
        "expiring": expiring_rewards(),
    })


@login_required
@module("cards_wallet", "Card")
def card_detail(request, pk: int):
    card = get_object_or_404(Card.objects.prefetch_related("rewards"), pk=pk)
    request.page_title = card.name

    return render(request, "cards/detail.html", {
        "card": card,
        "current": [r for r in card.rewards.all() if r.is_current],
        "lapsed": [r for r in card.rewards.all() if not r.is_current],
    })


@login_required
@module("cards_wallet", "Add card")
def card_create(request):
    if request.method == "POST":
        form = CardForm(request.POST)
        if form.is_valid():
            card = form.save()
            messages.success(
                request,
                f"{card.name} added. Now add what it earns, or it cannot be compared.",
            )
            return redirect("cards:reward_create", pk=card.pk)
        messages.error(request, "Check the highlighted fields.")
    else:
        form = CardForm()
    return render(request, "cards/card_form.html", {"form": form, "creating": True})


@login_required
@module("cards_wallet", "Edit card")
def card_edit(request, pk: int):
    card = get_object_or_404(Card, pk=pk)
    if request.method == "POST":
        form = CardForm(request.POST, instance=card)
        if form.is_valid():
            form.save()
            messages.success(request, f"{card.name} updated.")
            return redirect("cards:card_detail", pk=card.pk)
        messages.error(request, "Check the highlighted fields.")
    else:
        form = CardForm(instance=card)
    return render(
        request, "cards/card_form.html",
        {"form": form, "creating": False, "card": card},
    )


@login_required
@module("cards_wallet", "Add earning rule")
def reward_create(request, pk: int):
    card = get_object_or_404(Card, pk=pk)
    if request.method == "POST":
        form = RewardForm(request.POST)
        if form.is_valid():
            reward = form.save(commit=False)
            reward.card = card
            reward.save()
            messages.success(request, "Rule added.")
            return redirect("cards:card_detail", pk=card.pk)
        messages.error(request, "Check the highlighted fields.")
    else:
        form = RewardForm()
    return render(
        request, "cards/reward_form.html",
        {"form": form, "card": card, "creating": True},
    )


@login_required
@module("cards_wallet", "Edit earning rule")
def reward_edit(request, pk: int):
    reward = get_object_or_404(Reward.objects.select_related("card"), pk=pk)
    if request.method == "POST":
        form = RewardForm(request.POST, instance=reward)
        if form.is_valid():
            form.save()
            messages.success(request, "Rule updated.")
            return redirect("cards:card_detail", pk=reward.card.pk)
        messages.error(request, "Check the highlighted fields.")
    else:
        form = RewardForm(instance=reward)
    return render(
        request, "cards/reward_form.html",
        {"form": form, "card": reward.card, "creating": False, "reward": reward},
    )


@login_required
@require_POST
def reward_delete(request, pk: int):
    reward = get_object_or_404(Reward.objects.select_related("card"), pk=pk)
    card_pk = reward.card.pk
    reward.delete()
    messages.success(request, "Rule removed.")
    return redirect("cards:card_detail", pk=card_pk)
