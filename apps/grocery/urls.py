from django.urls import path

from . import views

app_name = "grocery"

urlpatterns = [
    path("", views.commodities, name="commodities"),
    path("map/", views.grocery_map, name="map"),
    path("stores.json", views.stores_json, name="stores_json"),
    path("<int:pk>/", views.commodity_detail, name="commodity_detail"),
    path("<int:pk>/track/", views.commodity_track, name="commodity_track"),
]
