from django.urls import path, include
from rest_framework.routers import DefaultRouter
from billing.api.views import (
    BillingContextViewSet, PaymentGatewayViewSet, PaymentMethodViewSet,
    APITokenViewSet, OutboundWebhookViewSet, AICreditPackageViewSet,
    AICreditTopUpViewSet, AIUsageLogViewSet, AICreditLotViewSet
)

router = DefaultRouter()
router.register(r'context', BillingContextViewSet, basename='billing-context')
router.register(r'gateways', PaymentGatewayViewSet, basename='payment-gateways')
router.register(r'methods', PaymentMethodViewSet, basename='payment-methods')
router.register(r'tokens', APITokenViewSet, basename='api-tokens')
router.register(r'webhooks', OutboundWebhookViewSet, basename='outbound-webhooks')
router.register(r'credit-packages', AICreditPackageViewSet, basename='ai-credit-packages')
router.register(r'top-ups', AICreditTopUpViewSet, basename='ai-credit-topups')
router.register(r'ai-usage', AIUsageLogViewSet, basename='ai-usage')
router.register(r'ai-credit-lots', AICreditLotViewSet, basename='ai-credit-lots')

urlpatterns = [
    path('', include(router.urls)),
]
