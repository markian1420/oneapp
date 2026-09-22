from django.urls import path

from . import views

app_name = "fuel"

urlpatterns = [
    path("", views.station_map, name="map"),
    path("stations.json", views.stations_json, name="stations_json"),
    path("stations/<int:pk>/", views.station_detail, name="station_detail"),
    path("stations/<int:pk>/favorite/", views.station_favorite, name="station_favorite"),
    path("advisory/", views.advisory, name="advisory"),
]
