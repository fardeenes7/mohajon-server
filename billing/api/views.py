from rest_framework import viewsets, status, generics
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from shops.api.views import ShopDetailView
from billing.models import (
    ShopSubscription, PaymentGatewayConfig, PaymentMethod, 
    MerchantAPIToken, OutboundWebhook, AICreditPackage, AICreditTopUp
)
from ai.models import AIUsageLog, AICreditLot
from billing.api.serializers import (
    ShopSubscriptionSerializer, PaymentGatewayConfigSerializer, PaymentMethodSerializer,
    MerchantAPITokenSerializer, OutboundWebhookSerializer, AICreditPackageSerializer,
    AICreditTopUpSerializer, AIUsageLogSerializer, AICreditLotSerializer
)
from billing.services.ai_credits import AICreditService
# ... other imports ...

class AICreditLotViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AICreditLotSerializer

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        qs = AICreditLot.objects.filter(shop_id=shop_id)
        
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
            
        return qs.order_by('-created_at')

class AICreditTopUpViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AICreditTopUpSerializer

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        qs = AICreditTopUp.objects.filter(shop_id=shop_id, deleted_at__isnull=True)
        
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
            
        return qs.order_by('-created_at')

    @action(detail=False, methods=['post'], url_path='initiate')
    def initiate(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        from shops.models import Shop
        shop = Shop.objects.get(id=shop_id)
        
        package_id = request.data.get('package_id')
        callback_url = request.data.get('callback_url')
        
        if not package_id or not callback_url:
            return Response({"error": "package_id and callback_url are required"}, status=status.HTTP_400_BAD_REQUEST)
            
        service = AICreditService(shop)
        try:
            res = service.initiate_topup(package_id, callback_url)
            return Response(res)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='finalize')
    def finalize(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        from shops.models import Shop
        shop = Shop.objects.get(id=shop_id)
        
        payment_id = request.data.get('payment_id')
        if not payment_id:
            return Response({"error": "payment_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        service = AICreditService(shop)
        try:
            res = service.finalize_topup(payment_id)
            return Response(res)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

class AICreditPackageViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Publicly list available AI credit packages.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = AICreditPackageSerializer
    queryset = AICreditPackage.objects.filter(is_active=True).order_by("sort_order")
from billing.services.subscription import get_subscription_context
from billing.services.gateway import set_gateway_credentials, ensure_shop_payment_methods
from billing.services.developer import create_api_token

class BillingContextViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def _get_shop(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        from shops.models import Shop
        return Shop.objects.get(id=shop_id)

    @action(detail=False, methods=['post'], url_path='initiate-payment')
    def initiate_payment(self, request):
        from billing.services.subscription import BillingService
        shop = self._get_shop(request)
        service = BillingService(shop)
        
        plan_id = request.data.get('plan_id')
        callback_url = request.data.get('callback_url')
        
        res = service.initiate_subscription_payment(plan_id, callback_url)
        return Response(res)

    @action(detail=False, methods=['post'], url_path='finalize-payment')
    def finalize_payment(self, request):
        from billing.services.subscription import BillingService
        shop = self._get_shop(request)
        service = BillingService(shop)
        
        payment_id = request.data.get('payment_id')
        res = service.finalize_subscription_payment(payment_id)
        return Response(res)

class PaymentGatewayViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PaymentGatewayConfigSerializer

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        return PaymentGatewayConfig.objects.filter(shop_id=shop_id, deleted_at__isnull=True)

    @action(detail=False, methods=['post'], url_path='configure')
    def configure(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        from shops.models import Shop
        shop = Shop.objects.get(id=shop_id)
        
        gateway = request.data.get('gateway')
        credentials = request.data.get('credentials')
        is_test_mode = request.data.get('is_test_mode', False)
        
        if not gateway or not credentials:
            return Response({"error": "Gateway and credentials are required"}, status=status.HTTP_400_BAD_REQUEST)
            
        config = set_gateway_credentials(shop, gateway, credentials, is_test_mode)
        return Response(self.get_serializer(config).data)

class PaymentMethodViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PaymentMethodSerializer

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        from shops.models import Shop
        shop = Shop.objects.get(id=shop_id)
        ensure_shop_payment_methods(shop)
        return PaymentMethod.objects.filter(
            shop_id=shop_id,
            deleted_at__isnull=True,
            method__in=PaymentMethod.ALLOWED_METHODS,
        )

    def perform_create(self, serializer):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        serializer.save(shop_id=shop_id, tenant_id=shop_id)

    def perform_update(self, serializer):
        instance = serializer.instance
        is_enabled = serializer.validated_data.get(
            'is_enabled',
            instance.is_enabled,
        )
        if instance.is_enabled and not is_enabled:
            shop_id = ShopDetailView()._resolve_shop_id(self.request)
            
            active_gateways = set(PaymentGatewayConfig.objects.filter(
                shop_id=shop_id,
                is_active=True,
                deleted_at__isnull=True,
            ).values_list('gateway', flat=True))
            
            is_currently_valid = instance.method == PaymentMethod.METHOD_COD or instance.method in active_gateways
            
            if is_currently_valid:
                enabled_methods = PaymentMethod.objects.filter(
                    shop_id=shop_id,
                    is_enabled=True,
                    deleted_at__isnull=True,
                ).values_list('method', flat=True)
                
                valid_enabled_count = sum(
                    1 for m in enabled_methods if m == PaymentMethod.METHOD_COD or m in active_gateways
                )
                
                if valid_enabled_count <= 1:
                    raise ValidationError(
                        {
                            "is_enabled": (
                                "At least one connected payment method must remain active."
                            )
                        }
                    )
        serializer.save()

class APITokenViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MerchantAPITokenSerializer

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        return MerchantAPIToken.objects.filter(shop_id=shop_id, deleted_at__isnull=True)

    def create(self, request, *args, **kwargs):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        from shops.models import Shop
        shop = Shop.objects.get(id=shop_id)
        
        name = request.data.get('name')
        scopes = request.data.get('scopes', [])
        expires_in_days = request.data.get('expires_in_days')
        
        instance, raw_token = create_api_token(shop, request.user, name, scopes, expires_in_days)
        
        serializer = self.get_serializer(instance)
        data = serializer.data
        data['raw_token'] = raw_token  # ONLY shown once
        return Response(data, status=status.HTTP_201_CREATED)

class OutboundWebhookViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = OutboundWebhookSerializer

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        return OutboundWebhook.objects.filter(shop_id=shop_id, deleted_at__isnull=True)

    def perform_create(self, serializer):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        serializer.save(shop_id=shop_id, tenant_id=shop_id)

class AIUsageLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AIUsageLogSerializer

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        qs = AIUsageLog.objects.filter(shop_id=shop_id)
        
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
            
        return qs.order_by('-created_at')
