"""The daily briefing."""

from __future__ import annotations

from django.shortcuts import render

from apps.core.views import module

from apps.core.freshness import summary as freshness_summary

from .services import REQUIREMENTS, build_briefing


@module("insights", "Today")
def briefing(request):
    return render(request, "insights/briefing.html", {
        "briefing": build_briefing(),
        "total_possible": len(REQUIREMENTS),
        # Every insight below is only as current as the feed behind it, so the
        # freshness of those feeds belongs on the same screen.
        "freshness": freshness_summary(),
    })
