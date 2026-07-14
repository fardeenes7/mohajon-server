from django.contrib import admin

from ai.models import AIModelRegistry


@admin.register(AIModelRegistry)
class AIModelRegistryAdmin(admin.ModelAdmin):
	list_display = (
		"usage",
		"provider",
		"model_name",
		"is_active",
		"is_default",
		"priority",
		"updated_at",
	)
	list_filter = ("usage", "provider", "is_active", "is_default")
	search_fields = ("model_name", "display_name")
	ordering = ("usage", "priority", "model_name")
