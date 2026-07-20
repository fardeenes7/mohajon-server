from abc import ABC, abstractmethod


class CourierInterface(ABC):
    """
    Port that every courier provider implements. Providers resolve their own
    per-shop credentials internally (via shipping.services.get_courier_credentials)
    so callers only pass a shop_id and normalized DTOs.

    Normalized DTOs use snake_case keys shared across providers; each adapter is
    responsible for translating to/from its vendor-specific schema.
    """

    @property
    @abstractmethod
    def provider_code(self) -> str:
        """The identifier for this courier provider (e.g. 'PATHAO', 'STEADFAST')."""
        pass

    @abstractmethod
    def parse_webhook_payload(self, payload: dict) -> dict:
        """
        Parse the courier's raw webhook payload into our standard format.
        Returns a dict with:
          - external_consignment_id: str
          - order_id: str
          - status: str (from CourierConsignmentStatus)
          - tracking_code: str
          - success_rate: float | None (delivery success rate if provided by the courier)
        """
        pass

    def verify_webhook_signature(self, shop_id: str | None, signature: str, body: bytes) -> bool:
        """
        Validate the cryptographic signature of an incoming webhook payload.

        Default implementation returns False (deny). Providers that support
        signed webhooks override this. shop_id may be None when the provider
        cannot be resolved to a shop before verification.
        """
        return False

    @abstractmethod
    def create_consignment(self, shop_id: str, order: dict) -> dict:
        """
        Create a shipment with the courier.

        `order` is a normalized dict:
          - order_id, merchant_order_id: str
          - recipient_name, recipient_phone, recipient_address: str
          - amount_to_collect: float (0 for prepaid)
          - item_quantity: int
          - item_weight: float (kg)
          - recipient_city, recipient_zone, recipient_area: int | None
          - special_instruction: str

        Returns a normalized dict:
          - external_consignment_id: str
          - tracking_code: str
          - status: str (from CourierConsignmentStatus)
          - payload: dict (raw provider response)
        """
        pass

    @abstractmethod
    def calculate_price(self, shop_id: str, price_request: dict) -> dict:
        """
        Estimate delivery price.

        `price_request` keys: item_type, delivery_type, item_weight,
        recipient_city, recipient_zone. Returns the raw provider price dict.
        """
        pass

    @abstractmethod
    def list_cities(self, shop_id: str) -> list[dict]:
        """Return the courier's serviceable cities as [{'id', 'name'}, ...]."""
        pass

    @abstractmethod
    def list_zones(self, shop_id: str, city_id: int) -> list[dict]:
        """Return zones for a city as [{'id', 'name'}, ...]."""
        pass

    @abstractmethod
    def list_areas(self, shop_id: str, zone_id: int) -> list[dict]:
        """Return areas for a zone as [{'id', 'name'}, ...]."""
        pass

    @abstractmethod
    def get_tracking(self, shop_id: str, external_consignment_id: str) -> dict:
        """
        Fetch the latest tracking info for a consignment.

        Returns a normalized dict:
          - status: str (from CourierConsignmentStatus)
          - tracking_code: str
          - payload: dict (raw provider response)
        """
        pass


class CourierRegistry:
    def __init__(self):
        self._providers: dict[str, CourierInterface] = {}

    def register(self, provider: CourierInterface) -> None:
        self._providers[provider.provider_code.upper()] = provider

    def get_provider(self, provider_code: str) -> CourierInterface | None:
        return self._providers.get(provider_code.upper())


# Global registry instance
courier_registry = CourierRegistry()
