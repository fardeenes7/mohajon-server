from django.urls import path

from users.api.customer_views import CustomerProfileDetailAPI, CustomerSearchAPI

urlpatterns = [
    path("customers/<uuid:user_id>/", CustomerProfileDetailAPI.as_view(), name="customer-profile-detail"),
    path("customers/by-phone/", CustomerSearchAPI.as_view(), name="customer-profile-by-phone"),
]
