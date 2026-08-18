from django.urls import path

from . import views

app_name = "places"

urlpatterns = [
    path("", views.place_map, name="map"),
    path("places.json", views.places_json, name="places_json"),
    path("where-am-i/", views.calibration, name="calibration"),
    path("<int:pk>/", views.place_detail, name="detail"),
    path("<int:pk>/favorite/", views.place_favorite, name="favorite"),
]
