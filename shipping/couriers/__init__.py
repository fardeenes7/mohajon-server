"""
Courier provider implementations.

Providers are registered into shipping.registry.courier_registry from
`register_couriers()`, invoked once from ShippingConfig.ready().
"""

from shipping.registry import courier_registry


def register_couriers() -> None:
    """Register all built-in courier providers. Idempotent."""
    from shipping.couriers.pathao.adapter import PathaoCourier

    courier_registry.register(PathaoCourier())
