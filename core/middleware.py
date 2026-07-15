import uuid
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.services.request_context import clear_request_context, set_request_context

class TenantMiddleware:
    """
    Middleware that captures the tenant context and enforces it at the database layer.
    Extracts the tenant dynamically via 'X-Tenant-ID' request header.
    To support PgBouncer, the SET LOCAL command only lasts for the current transaction block.
    If ATOMIC gets committed, we need a signal implementation, but here we inject it 
    as part of standard workflow queries.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_id = request.headers.get("X-Tenant-ID")

        if tenant_id:
            try:
                # Basic validation to prevent SQLi
                uuid.UUID(tenant_id)
                
                # Authorization check
                user = getattr(request, "user", None)
                print("DEBUG: middleware user", type(user), user.is_authenticated if hasattr(user, 'is_authenticated') else None)
                print("DEBUG: _force_auth_user", getattr(request, "_force_auth_user", "MISSING"))
                # DRF test client sets _force_auth_user on the HttpRequest
                if not user or not user.is_authenticated:
                    if getattr(request, "_force_auth_user", None):
                        user = request._force_auth_user
                
                if not user or not user.is_authenticated:
                    # Attempt DRF JWT Authentication
                    try:
                        from rest_framework_simplejwt.authentication import JWTAuthentication
                        from rest_framework.request import Request
                        
                        # JWTAuthentication requires a DRF Request object to check headers/cookies
                        drf_request = Request(request)
                        jwt_auth = JWTAuthentication()
                        auth_result = jwt_auth.authenticate(drf_request)
                        if auth_result:
                            user, token = auth_result
                            request.user = user
                    except Exception:
                        pass

                if not user or not user.is_authenticated:
                    return JsonResponse({"detail": "Authentication required to use X-Tenant-ID."}, status=401)
                
                from shops.models import ShopMember
                is_member = ShopMember.objects.filter(
                    user=user,
                    shop_id=tenant_id,
                    deleted_at__isnull=True,
                    shop__deleted_at__isnull=True,
                ).exists()
                
                if not is_member:
                    return JsonResponse({"detail": "You do not have access to this shop."}, status=403)
            except ValueError:
                tenant_id = None

        request.tenant_id = tenant_id

        impersonated_user_id = request.headers.get("X-Impersonate-User-ID")
        expires_at_raw = request.headers.get("X-Impersonation-Expires-At")
        is_impersonation = bool(impersonated_user_id)
        request.impersonation = {
            "active": False,
            "target_user_id": None,
            "expires_at": None,
            "actor_user_id": str(request.user.id) if getattr(request, "user", None) and request.user.is_authenticated else None,
        }

        if is_impersonation:
            if not getattr(request, "user", None) or not request.user.is_authenticated:
                return JsonResponse({"detail": "Authentication required for impersonation."}, status=401)
            if not request.user.is_staff:
                return JsonResponse({"detail": "Only internal staff can impersonate."}, status=403)
            if not expires_at_raw:
                return JsonResponse({"detail": "X-Impersonation-Expires-At is required."}, status=400)

            expires_at = parse_datetime(expires_at_raw)
            if not expires_at:
                return JsonResponse({"detail": "Invalid X-Impersonation-Expires-At format."}, status=400)
            if timezone.is_naive(expires_at):
                expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())
            if expires_at <= timezone.now():
                return JsonResponse({"detail": "Impersonation context expired."}, status=403)

            request.impersonation = {
                "active": True,
                "target_user_id": impersonated_user_id,
                "expires_at": expires_at.isoformat(),
                "actor_user_id": str(request.user.id),
            }

        set_request_context(
            {
                "tenant_id": tenant_id,
                "impersonation": request.impersonation,
                "ip_address": request.META.get("REMOTE_ADDR"),
            }
        )

        # Since Django opens a fresh transaction or just executes within the connection thread:
        with connection.cursor() as cursor:
            # Explicitly clear bypass in case of connection pool leakage
            cursor.execute("SET app.bypass_rls = 'off'")
            if tenant_id:
                # `app.current_shop_id` will be used in PostgreSQL RLS policies
                cursor.execute("SET app.current_shop_id = %s", [tenant_id])
            else:
                cursor.execute("SET app.current_shop_id = ''")

        response = self.get_response(request)

        # Clear it structurally ensuring no connection pooling leak if transaction mode is weird
        with connection.cursor() as cursor:
            cursor.execute("SET app.current_shop_id = ''")
            cursor.execute("SET app.bypass_rls = 'off'")

        clear_request_context()

        return response
