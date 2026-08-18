from django.urls import path

from . import views

app_name = "spend"

urlpatterns = [
    path("", views.purchases, name="purchases"),
    path("new/", views.purchase_create, name="purchase_create"),
    path("<int:pk>/", views.purchase_detail, name="purchase_detail"),
    path("<int:pk>/delete/", views.purchase_delete, name="purchase_delete"),
    path("items/<int:pk>/wear/", views.item_wear, name="item_wear"),
    path("wardrobe/", views.wardrobe_screen, name="wardrobe"),
    path("promos/", views.promos, name="promos"),
    path("promos/<int:pk>/delete/", views.promo_delete, name="promo_delete"),
]
