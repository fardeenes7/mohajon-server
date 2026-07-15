import logging
from urllib.parse import urlencode
import requests
from django.utils.crypto import get_random_string
from django.core.cache import cache
from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from chat.api.serializers import WhatsAppConfigSerializer, WhatsAppOAuthPageSerializer
from chat.models import WhatsAppConfig

logger = logging.getLogger(__name__)

class ShopScopedAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def require_shop_id(self, request):
        shop_id = getattr(request, "tenant_id", None)
        if not shop_id:
            raise PermissionError("No shop context. Provide X-Tenant-ID header.")
        return str(shop_id)

    def handle_exception(self, exc):
        if isinstance(exc, PermissionError):
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)


class WhatsAppOAuthStartView(ShopScopedAPIView):
    @extend_schema(tags=["chat"])
    def post(self, request):
        shop_id = self.require_shop_id(request)
        app_id = getattr(settings, "META_APP_ID", "")
        if not app_id:
            return Response({"detail": "META_APP_ID is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        state = f"wa_{get_random_string(29)}"
        redirect_uri = getattr(settings, "META_OAUTH_REDIRECT_URI", "") or request.build_absolute_uri("/api/v1/chat/whatsapp/oauth/callback/")

        cache.set(f"whatsapp_oauth_state:{shop_id}:{state}", "1", timeout=60 * 10)


        auth_query = urlencode(
            {
                "client_id": app_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": "whatsapp_business_management,whatsapp_business_messaging",
                "response_type": "code",
            }
        )
        auth_url = f"https://www.facebook.com/v21.0/dialog/oauth?{auth_query}"

        return Response({"auth_url": auth_url, "state": state, "redirect_uri": redirect_uri})


class WhatsAppOAuthCallbackView(ShopScopedAPIView):
    @extend_schema(tags=["chat"])
    def post(self, request):
        shop_id = self.require_shop_id(request)
        code = request.data.get("code")
        state = request.data.get("state")
        
        if not code or not state:
            return Response({"detail": "code and state are required."}, status=status.HTTP_400_BAD_REQUEST)
            
        cached_state = cache.get(f"whatsapp_oauth_state:{shop_id}:{state}")
        if not cached_state:
            return Response({"detail": "Invalid or expired OAuth state."}, status=status.HTTP_400_BAD_REQUEST)

        app_id = getattr(settings, "META_APP_ID", "")
        app_secret = getattr(settings, "META_APP_SECRET", "")
        redirect_uri = getattr(settings, "META_OAUTH_REDIRECT_URI", "") or request.build_absolute_uri("/api/v1/chat/whatsapp/oauth/callback/")

        try:
            token_response = requests.get(
                "https://graph.facebook.com/v21.0/oauth/access_token",
                params={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
                timeout=20,
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            user_access_token = token_payload.get("access_token")
            
            if not user_access_token:
                return Response({"detail": "Token missing from response."}, status=status.HTTP_400_BAD_REQUEST)
                
            # Fetch User's WhatsApp Business Accounts and Phone Numbers
            # First, fetch WABAs
            waba_response = requests.get(
                "https://graph.facebook.com/v21.0/me/businesses",
                params={"access_token": user_access_token, "fields": "id,name,owned_whatsapp_business_accounts"},
                timeout=20,
            )
            waba_response.raise_for_status()
            waba_data = waba_response.json().get("data", [])
            
            phone_numbers = []
            for business in waba_data:
                waba_accounts = business.get("owned_whatsapp_business_accounts", {}).get("data", [])
                for account in waba_accounts:
                    waba_id = account.get("id")
                    
                    # Fetch phone numbers for this WABA
                    phones_resp = requests.get(
                        f"https://graph.facebook.com/v21.0/{waba_id}/phone_numbers",
                        params={"access_token": user_access_token},
                        timeout=20
                    )
                    phones_resp.raise_for_status()
                    for phone in phones_resp.json().get("data", []):
                        phone_numbers.append({
                            "id": phone.get("id"),
                            "display_phone_number": phone.get("display_phone_number"),
                            "name": phone.get("verified_name") or phone.get("display_phone_number"),
                            "waba_id": waba_id
                        })
            
            if not phone_numbers:
                return Response({"detail": "No WhatsApp Business phone numbers found."}, status=status.HTTP_400_BAD_REQUEST)

            # Store the access token temporarily mapped to this state so we can save it once a number is selected
            cache.set(f"whatsapp_oauth_token:{shop_id}:{state}", user_access_token, timeout=60 * 15)
            cache.set(f"whatsapp_oauth_phones:{shop_id}:{state}", phone_numbers, timeout=60 * 15)
            
            return Response({"oauth_state": state, "pages": WhatsAppOAuthPageSerializer(phone_numbers, many=True).data})

        except requests.RequestException as exc:
            return Response({"detail": f"Meta API request failed: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)


class WhatsAppOAuthSaveView(ShopScopedAPIView):
    @extend_schema(tags=["chat"])
    def post(self, request):
        shop_id = self.require_shop_id(request)
        state = request.data.get("oauth_state")
        selected_phone_id = request.data.get("selected_phone_id")
        
        if not state or not selected_phone_id:
            return Response({"detail": "oauth_state and selected_phone_id are required."}, status=status.HTTP_400_BAD_REQUEST)
            
        access_token = cache.get(f"whatsapp_oauth_token:{shop_id}:{state}")
        phone_numbers = cache.get(f"whatsapp_oauth_phones:{shop_id}:{state}")
        
        if not access_token or not phone_numbers:
            return Response({"detail": "OAuth session expired."}, status=status.HTTP_400_BAD_REQUEST)
            
        selected_phone = next((p for p in phone_numbers if str(p.get("id")) == str(selected_phone_id)), None)
        if not selected_phone:
            return Response({"detail": "Selected phone number not found in session."}, status=status.HTTP_400_BAD_REQUEST)
            
        config, _ = WhatsAppConfig.objects.update_or_create(
            shop_id=shop_id,
            defaults={
                "tenant_id": shop_id,
                "phone_number_id": selected_phone["id"],
                "waba_id": selected_phone["waba_id"],
                "access_token": access_token,
                "is_active": True
            }
        )
        
        # Cleanup cache
        cache.delete(f"whatsapp_oauth_state:{shop_id}:{state}")
        cache.delete(f"whatsapp_oauth_token:{shop_id}:{state}")
        cache.delete(f"whatsapp_oauth_phones:{shop_id}:{state}")
        
        return Response(WhatsAppConfigSerializer(config).data)


class WhatsAppConfigView(ShopScopedAPIView):
    @extend_schema(tags=["chat"])
    def get(self, request):
        shop_id = self.require_shop_id(request)
        try:
            config = WhatsAppConfig.objects.get(shop_id=shop_id, is_active=True)
            return Response(WhatsAppConfigSerializer(config).data)
        except WhatsAppConfig.DoesNotExist:
            return Response(None)
            
    @extend_schema(tags=["chat"])
    def delete(self, request):
        shop_id = self.require_shop_id(request)
        WhatsAppConfig.objects.filter(shop_id=shop_id).update(is_active=False)
        return Response(status=status.HTTP_204_NO_CONTENT)
