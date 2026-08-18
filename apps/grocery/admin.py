from django.contrib import admin

from .models import Commodity, CommodityPrice


@admin.register(Commodity)
class CommodityAdmin(admin.ModelAdmin):
    list_display = ("name", "specification", "category", "unit", "is_tracked")
    list_filter = ("category", "is_tracked")
    search_fields = ("name", "specification")


@admin.register(CommodityPrice)
class CommodityPriceAdmin(admin.ModelAdmin):
    list_display = ("observed_on", "commodity", "price", "region", "source")
    list_filter = ("source", "region", "observed_on")
    search_fields = ("commodity__name",)
    date_hierarchy = "observed_on"
