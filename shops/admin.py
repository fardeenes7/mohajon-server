from django.contrib import admin

from shops.models import Shop


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ("name", "subdomain", "plan", "base_currency")
    search_fields = ("name", "subdomain", "custom_domain")
    list_filter = ("plan", "base_currency", "is_billing_exempt")
    ordering = ("name",)
