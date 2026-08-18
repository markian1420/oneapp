"""The daily briefing."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.core.views import module

from .services import REQUIREMENTS, build_briefing


@login_required
@module("insights", "Today")
def briefing(request):
    return render(request, "insights/briefing.html", {
        "briefing": build_briefing(),
        "total_possible": len(REQUIREMENTS),
    })
