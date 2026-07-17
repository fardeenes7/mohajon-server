from django.urls import path, include
from rest_framework.routers import DefaultRouter

from orders.api.views import (
    StorefrontPaymentInvoiceCodConfirmView,
    StorefrontPaymentInvoiceDetailView,
    StorefrontCheckoutView,
    POSCheckoutView,
    OrderViewSet,
    CustomerAddressPreloadView,
)

router = DefaultRouter()
router.register(r'', OrderViewSet, basename='order')

storefront_urlpatterns = [
    path(
        "<slug:shop_slug>/pay/<uuid:token>/",
        StorefrontPaymentInvoiceDetailView.as_view(),
        name="storefront-payment-invoice-detail",
    ),
    path(
        "<slug:shop_slug>/pay/<uuid:token>/cod-confirm/",
        StorefrontPaymentInvoiceCodConfirmView.as_view(),
        name="storefront-payment-invoice-cod-confirm",
    ),
    path(
        "<slug:shop_slug>/checkout/",
        StorefrontCheckoutView.as_view(),
        name="storefront-checkout",
    ),
]

dashboard_urlpatterns = [
    path(
        "pos/checkout/",
        POSCheckoutView.as_view(),
        name="pos-checkout",
    ),
    path(
        "customers/addresses/preload/",
        CustomerAddressPreloadView.as_view(),
        name="dashboard-address-preload",
    ),
    path("", include(router.urls)),
]
