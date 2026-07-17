from abc import ABC, abstractmethod

class CourierInterface(ABC):
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


class CourierRegistry:
    def __init__(self):
        self._providers: dict[str, CourierInterface] = {}

    def register(self, provider: CourierInterface) -> None:
        self._providers[provider.provider_code.upper()] = provider

    def get_provider(self, provider_code: str) -> CourierInterface | None:
        return self._providers.get(provider_code.upper())

# Global registry instance
courier_registry = CourierRegistry()
