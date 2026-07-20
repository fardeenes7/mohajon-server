from django.apps import AppConfig

class ShippingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shipping'

    def ready(self):
        from shipping.couriers import register_couriers
        register_couriers()
