from django.urls import path

from . import views

app_name = "spend"

urlpatterns = [
    path("where/", views.where, name="where"),
    path("card-promos/", views.card_promos, name="card_promos"),
]
