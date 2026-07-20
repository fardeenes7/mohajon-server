from django.urls import path, include
from rest_framework.routers import DefaultRouter
from shipping.api.webhooks import CourierWebhookView
from shipping.api.views import (
    CourierAccountViewSet,
    CourierLocationView,
    CourierPriceEstimateView,
    CourierConsignmentViewSet,
)

router = DefaultRouter()
router.register(r'accounts', CourierAccountViewSet, basename='courier-accounts')
router.register(r'consignments', CourierConsignmentViewSet, basename='courier-consignments')

urlpatterns = [
    path("webhooks/<str:provider_code>/", CourierWebhookView.as_view(), name="courier-webhook"),
    path("locations/", CourierLocationView.as_view(), name="courier-locations"),
    path("estimate-price/", CourierPriceEstimateView.as_view(), name="courier-price-estimate"),
    path("", include(router.urls)),
]

