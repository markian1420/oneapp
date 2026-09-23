"""Sidebar state available to every template."""

from django.conf import settings

from .navigation import grouped_modules


def navigation(request):
    user = getattr(request, "user", None)
    signed_in = bool(user and user.is_authenticated)

    return {
        "nav_groups": grouped_modules(include_private=signed_in),
        # Views set both of these on the request, so the sidebar knows which
        # row to light up and the topbar knows what to call the page. See
        # apps.core.views.module.
        "current_module": getattr(request, "current_module", ""),
        "page_title": getattr(request, "page_title", ""),
        "maintenance_screens": settings.MAINTENANCE_SCREENS,
    }
