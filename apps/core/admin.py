from django.contrib import admin

from .models import SourceRun


@admin.register(SourceRun)
class SourceRunAdmin(admin.ModelAdmin):
    list_display = ("source", "ran_at", "ok", "rows", "detail")
    list_filter = ("source", "ok")
    date_hierarchy = "ran_at"
