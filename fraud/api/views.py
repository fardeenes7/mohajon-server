from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from fraud.models import FraudReport, FraudConfig
from fraud.models import FraudEventType
from fraud.services.risk import check_customer_risk
from fraud.services.fraud_scoring import dispatch_fraud_event
from shops.models import Shop

class FraudViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'])
    def check_risk(self, request):
        """
        Check risk score for a phone number.
        """
        phone_number = request.data.get('phone_number')
        shop_id = getattr(request, 'tenant_id', None)
        if not shop_id:
            return Response({"error": "Shop context missing"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            shop = Shop.objects.get(id=shop_id)
            risk = check_customer_risk(shop, phone_number)
            return Response(risk)
        except Shop.DoesNotExist:
             return Response({"error": "Shop not found"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['post'])
    def report(self, request):
        """
        Report a customer for fraudulent behavior.
        """
        phone_number = request.data.get('phone_number')
        customer_name = request.data.get('customer_name', '')
        reason = request.data.get('reason')
        notes = request.data.get('notes', '')
        order_id = request.data.get('order_id')
        merchant_id = request.data.get('merchant_id') or getattr(request.user, "id", None)
        
        shop_id = getattr(request, 'tenant_id', None)
        if not shop_id:
            return Response({"error": "Shop context missing"}, status=status.HTTP_400_BAD_REQUEST)
            
        if not phone_number or not reason:
            return Response({"error": "phone_number and reason are required"}, status=status.HTTP_400_BAD_REQUEST)

        report = FraudReport.objects.create(
            shop_id=shop_id,
            phone_number=phone_number,
            customer_name=customer_name,
            reason=reason,
            notes=notes,
            order_id=order_id,
            merchant_id=merchant_id,
        )
        if order_id:
            from orders.models import Order

            order = Order.objects.filter(id=order_id).select_related("phone_identity", "user").first()
            if order:
                dispatch_fraud_event(
                    order,
                    event_type=FraudEventType.FRAUD_REPORT_ADDED,
                    base_penalty=report.weight,
                    metadata={"report_id": str(report.id), "reason": reason, "notes": notes},
                )

        return Response({"status": "Reported successfully", "id": str(report.id)})

    @action(detail=False, methods=['get', 'patch'])
    def config(self, request):
        """
        Get or update fraud configuration.
        """
        shop_id = getattr(request, 'tenant_id', None)
        if not shop_id:
            return Response({"error": "Shop context missing"}, status=status.HTTP_400_BAD_REQUEST)

        config, _ = FraudConfig.objects.get_or_create(shop_id=shop_id)
        
        if request.method == 'PATCH':
            # Simple manual update for now
            if 'opt_in_pooling' in request.data:
                config.opt_in_pooling = request.data['opt_in_pooling']
            if 'block_high_risk' in request.data:
                config.block_high_risk = request.data['block_high_risk']
            if 'warn_on_rto' in request.data:
                config.warn_on_rto = request.data['warn_on_rto']
            if 'rto_threshold' in request.data:
                config.rto_threshold = request.data['rto_threshold']
            config.save()
            
        return Response({
            "opt_in_pooling": config.opt_in_pooling,
            "block_high_risk": config.block_high_risk,
            "warn_on_rto": config.warn_on_rto,
            "rto_threshold": config.rto_threshold
        })
