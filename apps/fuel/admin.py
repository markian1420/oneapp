from django.contrib import admin

from .models import DOEAdvisory, FillUp, PriceObservation, Vehicle


@admin.register(PriceObservation)
class PriceObservationAdmin(admin.ModelAdmin):
    list_display = ("place", "fuel_type", "price", "observed_at", "source")
    list_filter = ("fuel_type", "source")
    search_fields = ("place__name", "place__brand")
    autocomplete_fields = ("place",)


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
        "filled_at", "place", "vehicle", "fuel_type",
        "liters", "price_per_liter", "total_cost",
    )
    list_filter = ("fuel_type", "vehicle", "is_full_tank")
    autocomplete_fields = ("place",)
    date_hierarchy = "filled_at"
