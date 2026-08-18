from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    # Served from the root so the worker's scope covers the whole site.
    path(
        "sw.js",
        TemplateView.as_view(
            template_name="sw.js", content_type="application/javascript"
        ),
        name="service_worker",
    ),
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="accounts/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("map/", include("apps.places.urls")),
    path("fuel/", include("apps.fuel.urls")),
    path("grocery/", include("apps.grocery.urls")),
    path("spend/", include("apps.spend.urls")),
    path("today/", include("apps.insights.urls")),
    path("", include("apps.core.urls")),
]
