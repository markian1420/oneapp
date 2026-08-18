from django.urls import path

from . import views

app_name = "fuel"

urlpatterns = [
    path("", views.station_map, name="map"),
    path("stations.json", views.stations_json, name="stations_json"),
    path("stations/", views.stations, name="stations"),
    path("stations/<int:pk>/", views.station_detail, name="station_detail"),
    path("stations/<int:pk>/favorite/", views.station_favorite, name="station_favorite"),
    path("fill-ups/", views.fill_ups, name="fillups"),
    path("fill-ups/new/", views.fill_up_create, name="fillup_create"),
    path("fill-ups/<int:pk>/", views.fill_up_edit, name="fillup_edit"),
    path("fill-ups/<int:pk>/delete/", views.fill_up_delete, name="fillup_delete"),
    path("advisory/", views.advisory, name="advisory"),
    path("vehicles/", views.vehicles, name="vehicles"),
    path("vehicles/new/", views.vehicle_create, name="vehicle_create"),
    path("vehicles/<int:pk>/", views.vehicle_edit, name="vehicle_edit"),
]
