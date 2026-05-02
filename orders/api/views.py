from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from shops.api.views import ShopDetailView
from rest_framework import viewsets
from rest_framework.decorators import action
from orders.models import Order

from orders.api.serializers import (
    PaymentInvoiceCodConfirmResponseSerializer,
    PaymentInvoicePublicSerializer,
)
from orders.services import (
    PaymentInvoiceGoneError,
    PaymentInvoiceNotFoundError,
    payment_invoice_assert_active,
    payment_invoice_consume,
    payment_invoice_get_for_shop,
    order_transition,
)
from orders.api.serializers import (
    OrderListSerializer,
    OrderDetailSerializer,
    OrderStatusTransitionSerializer,
)


from shops.models import Shop


class StorefrontPaymentInvoiceBaseView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get_shop(self, shop_slug: str) -> Shop:
        return Shop.objects.get(subdomain=shop_slug, deleted_at__isnull=True)


class StorefrontPaymentInvoiceDetailView(StorefrontPaymentInvoiceBaseView):
    @extend_schema(
        tags=["storefront", "orders"],
        responses={200: PaymentInvoicePublicSerializer},
        summary="Validate and fetch public payment invoice by token",
    )
    def get(self, request, shop_slug: str, token: str):
        try:
            shop = self.get_shop(shop_slug=shop_slug)
        except Shop.DoesNotExist:
            return Response({"detail": "Shop not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            invoice = payment_invoice_get_for_shop(token=token, shop_id=str(shop.id))
            payment_invoice_assert_active(invoice=invoice)
        except PaymentInvoiceNotFoundError:
            return Response({"detail": "Payment invoice not found."}, status=status.HTTP_404_NOT_FOUND)
        except PaymentInvoiceGoneError as exc:
            return Response({"detail": f"Payment invoice is {exc.args[0]}."}, status=status.HTTP_410_GONE)

        serializer = PaymentInvoicePublicSerializer(invoice)
        payload = serializer.data
        
        # Check fraud risk for warning
        from fraud.services.risk import check_customer_risk
        from fraud.models import FraudConfig
        
        customer_phone = ""
        if invoice.order.customer_profile:
            customer_phone = invoice.order.customer_profile.phone_number
            
        risk = check_customer_risk(shop, customer_phone, actor_reference=request.META.get('REMOTE_ADDR'))
        fraud_config, _ = FraudConfig.objects.get_or_create(shop=shop)
        
        payload["fraud_risk"] = risk
        payload["payment_methods"] = [
            {
                "code": "COD", 
                "label": "Cash on Delivery", 
                "enabled": not (risk['is_high_risk'] and fraud_config.block_high_risk),
                "warning": "High RTO risk detected" if risk['is_high_risk'] else None
            },
            {"code": "ONLINE", "label": "Pay Online", "enabled": False},
        ]
        return Response(payload)


class StorefrontPaymentInvoiceCodConfirmView(StorefrontPaymentInvoiceBaseView):
    @extend_schema(
        tags=["storefront", "orders"],
        request=None,
        responses={200: PaymentInvoiceCodConfirmResponseSerializer},
        summary="Confirm order via COD by consuming payment invoice token",
    )
    def post(self, request, shop_slug: str, token: str):
        try:
            shop = self.get_shop(shop_slug=shop_slug)
        except Shop.DoesNotExist:
            return Response({"detail": "Shop not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            invoice = payment_invoice_get_for_shop(token=token, shop_id=str(shop.id))
            payment_invoice_assert_active(invoice=invoice)
        except PaymentInvoiceNotFoundError:
            return Response({"detail": "Payment invoice not found."}, status=status.HTTP_404_NOT_FOUND)
        except PaymentInvoiceGoneError as exc:
            return Response({"detail": f"Payment invoice is {exc.args[0]}."}, status=status.HTTP_410_GONE)

        # Check fraud risk before consuming
        from fraud.services.risk import check_customer_risk
        from fraud.models import FraudConfig
        
        customer_phone = ""
        if invoice.order.customer_profile:
            customer_phone = invoice.order.customer_profile.phone_number
            
        risk = check_customer_risk(shop, customer_phone, actor_reference=request.META.get('REMOTE_ADDR'))
        fraud_config, _ = FraudConfig.objects.get_or_create(shop=shop)
        
        if risk['is_high_risk'] and fraud_config.block_high_risk:
            return Response(
                {"detail": "This number is flagged for high RTO risk. COD is disabled for this purchase."}, 
                status=status.HTTP_403_FORBIDDEN
            )

        # Consume token
        invoice = payment_invoice_consume(token=token, shop_id=str(shop.id))

        if invoice.order.status in {"PENDING", "AWAITING_PAYMENT"}:
            order_transition(
                order=invoice.order,
                to_status="CONFIRMED",
                reason="COD confirmation via payment invoice page",
            )

        return Response(
            {
                "order_id": invoice.order_id,
                "order_status": invoice.order.status,
                "invoice_token": invoice.token,
            }
        )


class StorefrontCheckoutView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["storefront", "orders"],
        summary="Create a new order from the storefront",
    )
    def post(self, request, shop_slug: str):
        try:
            shop = Shop.objects.get(subdomain=shop_slug, deleted_at__isnull=True)
        except Shop.DoesNotExist:
            return Response({"detail": "Shop not found."}, status=status.HTTP_404_NOT_FOUND)

        from orders.services.checkout import checkout_create_order
        
        items = request.data.get("items", [])
        payment_method = request.data.get("payment_method", "COD")
        customer_profile_id = request.data.get("customer_profile_id")

        try:
            order = checkout_create_order(
                shop_id=str(shop.id),
                items=items,
                customer_profile_id=customer_profile_id,
                payment_method=payment_method,
                actor_reference=request.META.get('REMOTE_ADDR'),
            )
            return Response({
                "id": order.id,
                "total": order.total_amount,
                "status": order.status,
                "currency": order.currency,
            }, status=status.HTTP_201_CREATED)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)



class POSCheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["dashboard", "pos"],
        summary="Process a retail sale from the POS",
    )
    def post(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        from orders.services.pos import POSService
        
        service = POSService(shop_id)
        
        items = request.data.get('items', [])
        payments = request.data.get('payments', [])
        customer_id = request.data.get('customer_id')
        
        try:
            order = service.process_pos_sale(items, payments, customer_id)
            return Response({
                "id": order.id,
                "total": order.total_amount,
                "status": order.status
            }, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        return Order.objects.filter(shop_id=shop_id).select_related('customer_profile').order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'list':
            return OrderListSerializer
        return OrderDetailSerializer

    @extend_schema(
        tags=["dashboard", "orders"],
        request=OrderStatusTransitionSerializer,
        responses={200: OrderDetailSerializer},
        summary="Transition an order to a new status",
    )
    @action(detail=True, methods=['post'])
    def transition(self, request, pk=None):
        order = self.get_object()
        serializer = OrderStatusTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        to_status = serializer.validated_data['to_status']
        reason = serializer.validated_data.get('reason', '')
        
        try:
            order_transition(
                order=order,
                to_status=to_status,
                reason=reason,
                user=request.user
            )
            return Response(OrderDetailSerializer(order).data)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

