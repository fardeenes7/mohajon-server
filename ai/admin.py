from django.contrib import admin

from ai.models import AICreditLot, AIModelRegistry


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


@admin.register(AICreditLot)
class AICreditLotAdmin(admin.ModelAdmin):
	"""
	Platform-admin entry form for granting AI credits to a shop ("team").

	Creating a lot here routes through ``grant_ai_credits`` so the shop's
	cached ``ShopSettings.ai_credit_balance`` stays in sync with the ledger.
	Granted amounts and computed state are read-only after creation to protect
	ledger integrity — adjust balances by adding a new lot, not editing one.
	"""

	list_display = (
		"shop",
		"category",
		"credits_granted",
		"credits_remaining",
		"expires_at",
		"is_expired",
		"is_exhausted",
		"created_by",
		"created_at",
	)
	list_filter = ("category", "is_expired", "is_exhausted")
	search_fields = ("shop__name", "shop__subdomain", "note")
	autocomplete_fields = ("shop",)
	ordering = ("-created_at",)

	def get_readonly_fields(self, request, obj=None):
		base = (
			"credits_remaining",
			"is_exhausted",
			"is_expired",
			"created_by",
			"source_topup",
			"created_at",
			"updated_at",
		)
		if obj is not None:
			# Lock grant-defining fields on edit; only the note can change.
			return base + ("shop", "category", "credits_granted", "expires_at")
		return base

	def get_fields(self, request, obj=None):
		if obj is None:
			return ("shop", "category", "credits_granted", "expires_at", "note")
		return (
			"shop",
			"category",
			"credits_granted",
			"credits_remaining",
			"expires_at",
			"is_exhausted",
			"is_expired",
			"source_topup",
			"note",
			"created_by",
			"created_at",
			"updated_at",
		)

	def save_model(self, request, obj, form, change):
		from ai.services.ai_credits import _sync_cached_balance

		if change:
			# Only mutable field is the note; save directly without touching
			# balances or the ledger draw logic.
			super().save_model(request, obj, form, change)
			return

		# New grant: populate ledger fields, persist obj (so admin logging /
		# redirect see the real lot), then reconcile the cached balance.
		obj.credits_remaining = obj.credits_granted
		obj.created_by = request.user
		obj.tenant_id = obj.shop_id
		super().save_model(request, obj, form, change)
		_sync_cached_balance(obj.shop_id)
