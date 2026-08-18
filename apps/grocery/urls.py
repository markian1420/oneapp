from django.urls import path

from . import views

app_name = "grocery"

urlpatterns = [
    path("", views.commodities, name="commodities"),
    path("<int:pk>/", views.commodity_detail, name="commodity_detail"),
    path("<int:pk>/track/", views.commodity_track, name="commodity_track"),
]
