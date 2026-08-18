from django.contrib import admin

from .models import Promo, Purchase, PurchaseItem


class ItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "where", "category", "total", "paid_with")
    list_filter = ("category",)
    date_hierarchy = "occurred_at"
    inlines = [ItemInline]


@admin.register(Promo)
class PromoAdmin(admin.ModelAdmin):
    list_display = ("title", "issuer", "brand", "category", "ends_on", "status")
    list_filter = ("category", "issuer", "brand")
