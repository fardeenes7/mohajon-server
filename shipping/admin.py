from django.contrib import admin

from shipping.models import CourierAccount, CourierConsignment


@admin.register(CourierConsignment)
class CourierConsignmentAdmin(admin.ModelAdmin):
    list_display = ("external_consignment_id", "provider", "status", "shop", "order", "created_at")
    list_filter = ("provider", "status")
    search_fields = ("external_consignment_id", "tracking_code", "order__id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(CourierAccount)
class CourierAccountAdmin(admin.ModelAdmin):
    list_display = ("provider", "shop", "label", "is_active", "is_test_mode", "default_store_id")
    list_filter = ("provider", "is_active", "is_test_mode")
    search_fields = ("shop__name", "label")
    # Credentials are write-only via the service layer; don't surface the blob.
    exclude = ("credentials_encrypted",)
