"""
shipping/couriers/pathao/adapter.py

Pathao implementation of the CourierInterface. Translates normalized DTOs to/from
Pathao's vendor schema and resolves per-shop credentials internally.
"""

from __future__ import annotations

import logging

from shipping.couriers.pathao.client import PathaoClient, PathaoError
from shipping.models import CourierConsignmentStatus, CourierProvider
from shipping.registry import CourierInterface

logger = logging.getLogger(__name__)

# Pathao delivery/item defaults (per API docs):
#   delivery_type 48 = Normal Delivery, item_type 2 = Parcel
DEFAULT_DELIVERY_TYPE = 48
DEFAULT_ITEM_TYPE = 2

# Pathao status slug → our normalized CourierConsignmentStatus.
# Webhook schema may vary; unknown slugs fall through to None (ignored by services).
PATHAO_STATUS_MAP: dict[str, str] = {
    "pending": CourierConsignmentStatus.CREATED,
    "picked": CourierConsignmentStatus.DISPATCHED,
    "pickup": CourierConsignmentStatus.DISPATCHED,
    "picked_up": CourierConsignmentStatus.DISPATCHED,
    "dispatched": CourierConsignmentStatus.DISPATCHED,
    "in_transit": CourierConsignmentStatus.IN_TRANSIT,
    "on_the_way": CourierConsignmentStatus.IN_TRANSIT,
    "delivered": CourierConsignmentStatus.DELIVERED,
    "partial_delivery": CourierConsignmentStatus.DELIVERED,
    "delivery_failed": CourierConsignmentStatus.FAILED,
    "cancelled": CourierConsignmentStatus.FAILED,
    "returned": CourierConsignmentStatus.RTO,
    "return": CourierConsignmentStatus.RTO,
    "returned_to_origin": CourierConsignmentStatus.RTO,
}


class PathaoCourier(CourierInterface):
    @property
    def provider_code(self) -> str:
        return CourierProvider.PATHAO

    # ── Credential resolution ─────────────────────────────────────────────────

    def _client(self, shop_id: str) -> PathaoClient:
        # Imported lazily to avoid a circular import (services imports the registry).
        from shipping.services import get_courier_account, get_courier_credentials

        account = get_courier_account(shop_id=shop_id, provider=self.provider_code)
        credentials = get_courier_credentials(shop_id=shop_id, provider=self.provider_code)
        if not account or not credentials:
            raise PathaoError(f"No active Pathao account configured for shop {shop_id}.")
        client = PathaoClient(
            shop_id=shop_id,
            credentials=credentials,
            is_test_mode=account.is_test_mode,
        )
        client.store_id = account.default_store_id
        return client

    # ── Webhooks ──────────────────────────────────────────────────────────────

    def parse_webhook_payload(self, payload: dict) -> dict:
        slug = str(payload.get("status_slug") or payload.get("status") or "").strip().lower()
        status = PATHAO_STATUS_MAP.get(slug, "")
        return {
            "external_consignment_id": str(payload.get("consignment_id") or ""),
            "order_id": str(payload.get("merchant_order_id") or ""),
            "status": status,
            "tracking_code": str(payload.get("consignment_id") or ""),
            "success_rate": payload.get("delivery_success_rate"),
        }

    def verify_webhook_signature(self, shop_id: str | None, signature: str, body: bytes) -> bool:
        from webhooks.services import webhook_signature_valid

        secret = None
        if shop_id:
            from shipping.services import get_courier_credentials

            creds = get_courier_credentials(shop_id=shop_id, provider=self.provider_code)
            if creds:
                secret = creds.get("webhook_secret")
        return webhook_signature_valid(signature=signature, body=body, app_secret=secret)

    # ── Consignments ──────────────────────────────────────────────────────────

    def create_consignment(self, shop_id: str, order: dict) -> dict:
        client = self._client(shop_id)
        store_id = getattr(client, "store_id", "") or ""
        if not store_id:
            raise PathaoError(
                f"Pathao account for shop {shop_id} has no default_store_id set."
            )

        pathao_order = {
            "store_id": store_id,
            "merchant_order_id": order.get("merchant_order_id") or order.get("order_id"),
            "recipient_name": order.get("recipient_name"),
            "recipient_phone": order.get("recipient_phone"),
            "recipient_address": order.get("recipient_address"),
            "delivery_type": order.get("delivery_type") or DEFAULT_DELIVERY_TYPE,
            "item_type": order.get("item_type") or DEFAULT_ITEM_TYPE,
            "item_quantity": order.get("item_quantity") or 1,
            "item_weight": order.get("item_weight") or 0.5,
            "amount_to_collect": order.get("amount_to_collect") or 0,
            "special_instruction": order.get("special_instruction") or "",
        }
        if order.get("recipient_city") is not None:
            pathao_order["recipient_city"] = order["recipient_city"]
        if order.get("recipient_zone") is not None:
            pathao_order["recipient_zone"] = order["recipient_zone"]
        if order.get("recipient_area") is not None:
            pathao_order["recipient_area"] = order["recipient_area"]

        response = client.create_order(pathao_order)
        data = response.get("data", response) if isinstance(response, dict) else {}
        consignment_id = str(data.get("consignment_id") or "")
        return {
            "external_consignment_id": consignment_id,
            "tracking_code": consignment_id,
            "status": CourierConsignmentStatus.CREATED,
            "payload": response,
        }

    def get_tracking(self, shop_id: str, external_consignment_id: str) -> dict:
        client = self._client(shop_id)
        response = client.get_order_info(external_consignment_id)
        data = response.get("data", response) if isinstance(response, dict) else {}
        slug = str(data.get("order_status_slug") or data.get("order_status") or "").strip().lower()
        return {
            "status": PATHAO_STATUS_MAP.get(slug, CourierConsignmentStatus.CREATED),
            "tracking_code": external_consignment_id,
            "payload": response,
        }

    # ── Pricing & locations ─────────────────────────────────────────────────

    def calculate_price(self, shop_id: str, price_request: dict) -> dict:
        client = self._client(shop_id)
        payload = {
            "store_id": getattr(client, "store_id", "") or price_request.get("store_id"),
            "item_type": price_request.get("item_type") or DEFAULT_ITEM_TYPE,
            "delivery_type": price_request.get("delivery_type") or DEFAULT_DELIVERY_TYPE,
            "item_weight": price_request.get("item_weight") or 0.5,
            "recipient_city": price_request.get("recipient_city"),
            "recipient_zone": price_request.get("recipient_zone"),
        }
        return client.calculate_price(payload)

    def list_cities(self, shop_id: str) -> list[dict]:
        response = self._client(shop_id).get_cities()
        return self._extract_locations(response, "city_id", "city_name")

    def list_zones(self, shop_id: str, city_id: int) -> list[dict]:
        response = self._client(shop_id).get_zones(city_id)
        return self._extract_locations(response, "zone_id", "zone_name")

    def list_areas(self, shop_id: str, zone_id: int) -> list[dict]:
        response = self._client(shop_id).get_areas(zone_id)
        return self._extract_locations(response, "area_id", "area_name")

    @staticmethod
    def _extract_locations(response: dict, id_key: str, name_key: str) -> list[dict]:
        data = response.get("data", {}) if isinstance(response, dict) else {}
        # Pathao nests the list under a schema-specific key (e.g. "data": {"data": [...]}).
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []
        return [
            {"id": item.get(id_key), "name": item.get(name_key)}
            for item in items
            if isinstance(item, dict)
        ]
