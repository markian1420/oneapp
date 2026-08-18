from django.contrib import admin

from .models import DOEAdvisory, FillUp, PriceObservation, Station, Vehicle


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("display_name", "brand", "city", "region", "is_favorite")
    list_filter = ("brand", "region", "is_favorite")
    search_fields = ("name", "brand", "city", "street")
    readonly_fields = ("osm_type", "osm_id", "first_imported_at", "last_seen_at")


@admin.register(PriceObservation)
class PriceObservationAdmin(admin.ModelAdmin):
    list_display = ("station", "fuel_type", "price", "observed_at", "source")
    list_filter = ("fuel_type", "source")
    search_fields = ("station__name", "station__brand")
    autocomplete_fields = ("station",)


@admin.register(DOEAdvisory)
class DOEAdvisoryAdmin(admin.ModelAdmin):
    list_display = ("week_of", "region", "brand", "fuel_type", "price")
    list_filter = ("region", "fuel_type", "week_of")


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("name", "plate", "default_fuel_type", "km_per_liter", "is_default")


@admin.register(FillUp)
class FillUpAdmin(admin.ModelAdmin):
    list_display = (
        "filled_at", "station", "vehicle", "fuel_type",
        "liters", "price_per_liter", "total_cost",
    )
    list_filter = ("fuel_type", "vehicle", "is_full_tank")
    autocomplete_fields = ("station",)
    date_hierarchy = "filled_at"
