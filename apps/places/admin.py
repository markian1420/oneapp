from django.contrib import admin

from .models import Place


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("display_name", "kind", "brand", "city", "region", "is_favorite")
    list_filter = ("kind", "brand", "region", "is_favorite")
    search_fields = ("name", "brand", "city", "street")
    readonly_fields = ("osm_type", "osm_id", "first_imported_at", "last_seen_at")
