from django.utils.crypto import get_random_string
from django.db import transaction
from django.core.cache import cache
from django.conf import settings
from urllib.parse import urlencode

import requests
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from marketing.serializers import (
    ProductSocialPostLogSerializer,
    SocialBulkPublishRequestSerializer,
    SocialConnectionCreateSerializer,
    SocialOAuthCallbackSerializer,
    SocialOAuthPageSerializer,
    SocialConnectionSerializer,
    SocialPublishRequestSerializer,
)
from marketing.selectors import list_product_social_logs, list_social_connections
from marketing.services import (
    create_social_publish_log,
    disconnect_social_connection,
    normalize_meta_page_payload,
    upsert_social_connection,
)
from marketing.tasks import publish_product_to_social
from marketing.models import SocialConnection
from marketing.api.throttles import SocialOAuthThrottle, SocialPublishThrottle


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


class SocialOAuthStartView(ShopScopedAPIView):
    throttle_classes = [SocialOAuthThrottle]

    @extend_schema(tags=["marketing"])
    def post(self, request):
        shop_id = self.require_shop_id(request)
        app_id = getattr(settings, "META_APP_ID", "")
        if not app_id:
            return Response({"detail": "META_APP_ID is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        state = get_random_string(32)
        redirect_uri = getattr(settings, "META_OAUTH_REDIRECT_URI", "") or request.build_absolute_uri("/api/v1/marketing/social/connect/callback/")

        cache.set(f"meta_oauth_state:{shop_id}:{state}", "1", timeout=60 * 10)

        auth_query = urlencode(
            {
                "client_id": app_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": "pages_manage_posts,pages_read_engagement,pages_show_list,pages_messaging,pages_manage_metadata,ads_management,ads_read,business_management",
                "response_type": "code",
            }
        )
        auth_url = f"https://www.facebook.com/v19.0/dialog/oauth?{auth_query}"

        return Response({"shop_id": shop_id, "provider": "META", "state": state, "redirect_uri": redirect_uri, "auth_url": auth_url})


class SocialOAuthCallbackView(ShopScopedAPIView):
    throttle_classes = [SocialOAuthThrottle]

    @extend_schema(request=SocialOAuthCallbackSerializer, tags=["marketing"])
    def post(self, request):
        shop_id = self.require_shop_id(request)
        serializer = SocialOAuthCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        selected_page_id = serializer.validated_data.get("selected_page_id", "")
        oauth_state = serializer.validated_data.get("oauth_state") or serializer.validated_data.get("state")

        if oauth_state and selected_page_id and not serializer.validated_data.get("code"):
            cached_pages = cache.get(f"meta_oauth_pages:{shop_id}:{oauth_state}")
            if not cached_pages:
                return Response({"detail": "OAuth session expired. Start connection again."}, status=status.HTTP_400_BAD_REQUEST)

            selected_page = next((page for page in cached_pages if str(page.get("id")) == str(selected_page_id)), None)
            if not selected_page:
                return Response({"detail": "Selected page not found in OAuth session."}, status=status.HTTP_400_BAD_REQUEST)

            connection = upsert_social_connection(
                shop_id=shop_id,
                provider="META",
                page_id=str(selected_page.get("id")),
                page_name=str(selected_page.get("name")),
                access_token=str(selected_page.get("access_token")),
                expires_in=60 * 24 * 60 * 60,
            )
            cache.delete(f"meta_oauth_pages:{shop_id}:{oauth_state}")
            return Response({"connection": SocialConnectionSerializer(connection).data})

        code = serializer.validated_data.get("code", "")
        state = serializer.validated_data.get("state", "")
        if not code or not state:
            return Response({"detail": "Provide code and state, or oauth_state and selected_page_id."}, status=status.HTTP_400_BAD_REQUEST)

        cached_state = cache.get(f"meta_oauth_state:{shop_id}:{state}")
        if not cached_state:
            return Response({"detail": "Invalid or expired OAuth state."}, status=status.HTTP_400_BAD_REQUEST)

        app_id = getattr(settings, "META_APP_ID", "")
        app_secret = getattr(settings, "META_APP_SECRET", "")
        redirect_uri = getattr(settings, "META_OAUTH_REDIRECT_URI", "") or request.build_absolute_uri("/api/v1/marketing/social/connect/callback/")
        if not app_id or not app_secret:
            return Response({"detail": "META_APP_ID / META_APP_SECRET are not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        try:
            token_response = requests.get(
                "https://graph.facebook.com/v19.0/oauth/access_token",
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
                return Response({"detail": "Meta OAuth token response missing access_token."}, status=status.HTTP_400_BAD_REQUEST)

            # 1. Fetch User Info (to save user-level token for Marketing API)
            user_info_resp = requests.get(
                "https://graph.facebook.com/v19.0/me",
                params={"access_token": user_access_token, "fields": "id,name"},
                timeout=10
            )
            user_info = user_info_resp.json()

            from marketing.models import MetaUserAccount
            MetaUserAccount.objects.update_or_create(
                shop_id=shop_id,
                defaults={
                    "tenant_id": shop_id,
                    "meta_user_id": user_info.get("id"),
                    "name": user_info.get("name"),
                    "access_token": user_access_token,
                }
            )

            # 2. Fetch Pages
            pages_response = requests.get(
                "https://graph.facebook.com/v19.0/me/accounts",
                params={
                    "access_token": user_access_token,
                    "fields": "id,name,access_token",
                },
                timeout=20,
            )
            pages_response.raise_for_status()
            pages_payload = pages_response.json()
            raw_pages = pages_payload.get("data", [])

        except requests.RequestException as exc:
            return Response({"detail": f"Meta API request failed: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

        pages = [normalize_meta_page_payload(page) for page in raw_pages if page.get("id") and page.get("name") and page.get("access_token")]
        if not pages:
            return Response({"detail": "No manageable Facebook pages found for this account."}, status=status.HTTP_400_BAD_REQUEST)

        if selected_page_id:
            selected_page = next((page for page in pages if str(page.get("id")) == str(selected_page_id)), None)
            if not selected_page:
                return Response({"detail": "Selected page was not returned by Meta."}, status=status.HTTP_400_BAD_REQUEST)

            connection = upsert_social_connection(
                shop_id=shop_id,
                provider="META",
                page_id=str(selected_page.get("id")),
                page_name=str(selected_page.get("name")),
                access_token=str(selected_page.get("access_token")),
                expires_in=60 * 24 * 60 * 60,
            )
            cache.delete(f"meta_oauth_state:{shop_id}:{state}")
            return Response({"connection": SocialConnectionSerializer(connection).data})

        cache.set(f"meta_oauth_pages:{shop_id}:{state}", pages, timeout=60 * 10)
        page_preview = [{"id": page["id"], "name": page["name"]} for page in pages]
        return Response({"oauth_state": state, "pages": SocialOAuthPageSerializer(page_preview, many=True).data})


class SocialConnectionListCreateView(ShopScopedAPIView):
    throttle_classes = [SocialOAuthThrottle]

    @extend_schema(responses={200: SocialConnectionSerializer(many=True)}, tags=["marketing"])
    def get(self, request):
        shop_id = self.require_shop_id(request)
        qs = list_social_connections(shop_id=shop_id)
        return Response(SocialConnectionSerializer(qs, many=True).data)

    @extend_schema(request=SocialConnectionCreateSerializer, responses={201: SocialConnectionSerializer}, tags=["marketing"])
    def post(self, request):
        shop_id = self.require_shop_id(request)
        serializer = SocialConnectionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = upsert_social_connection(
            shop_id=shop_id,
            provider=serializer.validated_data["provider"],
            page_id=serializer.validated_data["page_id"],
            page_name=serializer.validated_data["page_name"],
            access_token=serializer.validated_data["access_token"],
            expires_in=serializer.validated_data.get("expires_in"),
        )
        return Response(SocialConnectionSerializer(connection).data, status=status.HTTP_201_CREATED)


class SocialConnectionDisconnectView(ShopScopedAPIView):
    throttle_classes = [SocialOAuthThrottle]

    @extend_schema(tags=["marketing"])
    def post(self, request, connection_id):
        shop_id = self.require_shop_id(request)
        disconnect_social_connection(shop_id=shop_id, connection_id=connection_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SocialPublishView(ShopScopedAPIView):
    throttle_classes = [SocialPublishThrottle]

    @extend_schema(request=SocialPublishRequestSerializer, responses={202: ProductSocialPostLogSerializer}, tags=["marketing"])
    def post(self, request):
        shop_id = self.require_shop_id(request)
        serializer = SocialPublishRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        connection_exists = SocialConnection.objects.filter(
            id=serializer.validated_data["connection_id"],
            shop_id=shop_id,
            deleted_at__isnull=True,
        ).exists()

        if not connection_exists:
            return Response({"detail": "Connection not found for this shop."}, status=status.HTTP_404_NOT_FOUND)

        post_log, _created = create_social_publish_log(
            shop_id=shop_id,
            product_id=str(serializer.validated_data["product_id"]),
            connection_id=str(serializer.validated_data["connection_id"]),
            idempotency_key=serializer.validated_data.get("idempotency_key") or None,
        )
        transaction.on_commit(lambda: publish_product_to_social.delay(str(post_log.id)))

        return Response(ProductSocialPostLogSerializer(post_log).data, status=status.HTTP_202_ACCEPTED)


class SocialBulkPublishView(ShopScopedAPIView):
    throttle_classes = [SocialPublishThrottle]

    @extend_schema(request=SocialBulkPublishRequestSerializer, responses={202: ProductSocialPostLogSerializer(many=True)}, tags=["marketing"])
    def post(self, request):
        shop_id = self.require_shop_id(request)
        serializer = SocialBulkPublishRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        connection_exists = SocialConnection.objects.filter(
            id=serializer.validated_data["connection_id"],
            shop_id=shop_id,
            deleted_at__isnull=True,
        ).exists()
        if not connection_exists:
            return Response({"detail": "Connection not found for this shop."}, status=status.HTTP_404_NOT_FOUND)

        logs = []
        for product_id in serializer.validated_data["product_ids"]:
            post_log, _created = create_social_publish_log(
                shop_id=shop_id,
                product_id=str(product_id),
                connection_id=str(serializer.validated_data["connection_id"]),
            )
            transaction.on_commit(lambda post_log_id=str(post_log.id): publish_product_to_social.delay(post_log_id))
            logs.append(post_log)

        return Response(ProductSocialPostLogSerializer(logs, many=True).data, status=status.HTTP_202_ACCEPTED)


class ProductSocialActivityView(ShopScopedAPIView):
    @extend_schema(responses={200: ProductSocialPostLogSerializer(many=True)}, tags=["marketing"])
    def get(self, request, product_id):
        shop_id = self.require_shop_id(request)
        logs = list_product_social_logs(shop_id=shop_id, product_id=product_id)
        return Response(ProductSocialPostLogSerializer(logs, many=True).data)
