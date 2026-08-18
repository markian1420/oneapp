from django.contrib import admin

from .models import Card, Reward


class RewardInline(admin.TabularInline):
    model = Reward
    extra = 1


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("label", "issuer", "network", "annual_fee", "is_active")
    list_filter = ("is_active", "network", "issuer")
    inlines = [RewardInline]


@admin.register(Reward)
class RewardAdmin(admin.ModelAdmin):
    list_display = ("card", "kind", "category", "brand", "rate", "valid_to")
    list_filter = ("kind", "category")
