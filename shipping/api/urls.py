from django.urls import path
from shipping.api.webhooks import CourierWebhookView

urlpatterns = [
    path("webhooks/<str:provider_code>/", CourierWebhookView.as_view(), name="courier-webhook"),
]
