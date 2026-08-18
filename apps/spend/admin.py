from django.contrib import admin

from .models import Promo, Purchase, PurchaseItem


class ItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "where", "category", "total", "card")
    list_filter = ("category", "card")
    date_hierarchy = "occurred_at"
    inlines = [ItemInline]


@admin.register(Promo)
class PromoAdmin(admin.ModelAdmin):
    list_display = ("title", "brand", "category", "ends_on", "status")
    list_filter = ("category", "brand")
