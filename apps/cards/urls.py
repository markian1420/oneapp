from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("", views.wallet, name="wallet"),
    path("which/", views.which_card, name="which"),
    path("new/", views.card_create, name="card_create"),
    path("<int:pk>/", views.card_detail, name="card_detail"),
    path("<int:pk>/edit/", views.card_edit, name="card_edit"),
    path("<int:pk>/rewards/new/", views.reward_create, name="reward_create"),
    path("rewards/<int:pk>/edit/", views.reward_edit, name="reward_edit"),
    path("rewards/<int:pk>/delete/", views.reward_delete, name="reward_delete"),
]
