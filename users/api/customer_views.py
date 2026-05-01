from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.selectors import find_customer_by_phone, get_customer_profile


class CustomerProfileDetailAPI(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        profile = get_customer_profile(user_id)
        if not profile:
            return Response(status=status.HTTP_404_NOT_FOUND)

        user = profile["user"]
        primary_phone = profile["primary_phone"]
        user_profile = profile["user_profile"]
        phone_profile = profile["phone_profile"]
        order_stats = profile["order_stats"]

        return Response(
            {
                "basic_info": {
                    "id": user.id,
                    "created_at": user.date_joined,
                    "primary_phone": primary_phone.phone_number if primary_phone else None,
                    "facebook_linked": user.social_accounts.filter(provider="FACEBOOK").exists(),
                },
                "addresses": [
                    {
                        "id": address.id,
                        "contact_name": address.contact_name,
                        "contact_phone": address.contact_phone,
                        "street": address.street,
                        "city": address.city,
                        "postal_code": address.postal_code,
                    }
                    for address in user.addresses.all()
                ],
                "order_stats": {
                    "total": order_stats["total"],
                    "delivered": order_stats["delivered"],
                    "cancelled": order_stats["cancelled"],
                },
                "fraud_info": {
                    "user_score": user_profile.risk_score if user_profile else 0,
                    "user_risk_level": user_profile.risk_level if user_profile else "LOW",
                    "phone_score": phone_profile.risk_score if phone_profile else 0,
                    "phone_risk_level": phone_profile.risk_level if phone_profile else "LOW",
                    "trust_score": primary_phone.trust_score if primary_phone else 0,
                    "is_verified": primary_phone.is_verified if primary_phone else False,
                },
                "recent_fraud_events": [
                    {
                        "id": event.id,
                        "event_type": event.event_type,
                        "score_impact": event.score_impact,
                        "confidence_level": event.confidence_level,
                        "source_type": event.source_type,
                        "created_at": event.created_at,
                    }
                    for event in profile["recent_events"]
                ],
            }
        )


class CustomerSearchAPI(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        phone = request.query_params.get("phone", "")
        identity = find_customer_by_phone(phone)
        if identity and identity.user:
            return Response(
                {
                    "user_id": identity.user.id,
                    "phone": identity.phone_number,
                    "is_verified": identity.is_verified,
                    "trust_score": identity.trust_score,
                }
            )
        return Response(status=status.HTTP_404_NOT_FOUND)
