from django.db import transaction
from django.db.models import F
from rest_framework import generics, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.services.purchase_order import POService
from catalog.models import ProductVariant

from accounting.api.serializers import (
    AdminPayoutActionSerializer, 
    AdminPayoutSerializer,
    MerchantBalanceSerializer,
    MerchantPayoutSerializer,
    MerchantLedgerSerializer,
    PurchaseOrderSerializer,
    PayoutRequestSerializer,
)
from accounting.models import LedgerEntry, PlatformBalance, Payout, PurchaseOrder
from rest_framework import viewsets

from shops.api.views import ShopDetailView



class AdminSettlementListView(generics.ListAPIView):
    queryset = Payout.objects.select_related('shop').order_by('-created_at')
    serializer_class = AdminPayoutSerializer
    permission_classes = [IsAdminUser]


class AdminSettlementApproveView(generics.GenericAPIView):
    queryset = Payout.objects.select_related('shop').all()
    serializer_class = AdminPayoutActionSerializer
    permission_classes = [IsAdminUser]

    def post(self, request, pk, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            payout = self.get_queryset().select_for_update().get(pk=pk)
            if payout.status != Payout.STATUS_PENDING:
                return Response(
                    {'detail': 'Only pending payouts can be approved.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            payout.status = Payout.STATUS_PROCESSING
            payout.admin_note = serializer.validated_data.get('admin_note', '')
            payout.save(update_fields=['status', 'admin_note', 'updated_at'])

        return Response(AdminPayoutSerializer(payout).data, status=status.HTTP_200_OK)


class AdminSettlementRejectView(generics.GenericAPIView):
    queryset = Payout.objects.select_related('shop').all()
    serializer_class = AdminPayoutActionSerializer
    permission_classes = [IsAdminUser]

    def post(self, request, pk, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            payout = self.get_queryset().select_for_update().get(pk=pk)
            if payout.status != Payout.STATUS_PENDING:
                return Response(
                    {'detail': 'Only pending payouts can be rejected.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            balance, _ = PlatformBalance.objects.select_for_update().get_or_create(
                shop=payout.shop,
                defaults={'tenant_id': payout.shop_id},
            )

            balance.current_balance = F('current_balance') + payout.amount
            balance.total_withdrawn = F('total_withdrawn') - payout.amount
            balance.save(update_fields=['current_balance', 'total_withdrawn', 'updated_at'])

            payout.status = Payout.STATUS_FAILED
            payout.admin_note = serializer.validated_data.get('admin_note', '')
            payout.save(update_fields=['status', 'admin_note', 'updated_at'])

            LedgerEntry.objects.create(
                shop_id=payout.shop_id,
                tenant_id=payout.shop_id,
                entry_type=LedgerEntry.ENTRY_TYPE_ADJUSTMENT,
                amount=payout.amount,
                description=f'Payout rejection reversal: {payout.id}',
                payout=payout,
            )

        return Response(AdminPayoutSerializer(payout).data, status=status.HTTP_200_OK)


class MerchantBalanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        if not shop_id:
            return Response({"detail": "No accessible shop found."}, status=status.HTTP_404_NOT_FOUND)
            
        balance, _ = PlatformBalance.objects.get_or_create(
            shop_id=shop_id,
            defaults={'tenant_id': shop_id}
        )
        serializer = MerchantBalanceSerializer(balance)
        return Response(serializer.data)


class MerchantPayoutListView(generics.ListAPIView):
    serializer_class = MerchantPayoutSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        return Payout.objects.filter(shop_id=shop_id).order_by('-created_at')


class MerchantLedgerListView(generics.ListAPIView):
    serializer_class = MerchantLedgerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        return LedgerEntry.objects.filter(shop_id=shop_id).order_by('-created_at')


class MerchantPayoutRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        if not shop_id:
            return Response({"detail": "No accessible shop found."}, status=status.HTTP_404_NOT_FOUND)
            
        serializer = PayoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        amount = serializer.validated_data['amount']
        
        with transaction.atomic():
            balance = PlatformBalance.objects.select_for_update().get(shop_id=shop_id)
            if balance.current_balance < amount:
                return Response({"detail": "Insufficient balance."}, status=status.HTTP_400_BAD_REQUEST)
                
            balance.current_balance -= amount
            balance.save(update_fields=['current_balance', 'updated_at'])
            
            payout = Payout.objects.create(
                shop_id=shop_id,
                tenant_id=shop_id,
                amount=amount,
                bank_info=serializer.validated_data['bank_info'],
                status=Payout.STATUS_PENDING
            )
            
            LedgerEntry.objects.create(
                shop_id=shop_id,
                tenant_id=shop_id,
                entry_type=LedgerEntry.ENTRY_TYPE_PAYOUT,
                amount=-amount,
                description=f"Payout request: {payout.id}",
                payout=payout
            )
            
        return Response(MerchantPayoutSerializer(payout).data, status=status.HTTP_201_CREATED)


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    serializer_class = PurchaseOrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        return PurchaseOrder.objects.filter(shop_id=shop_id).order_by('-created_at')

    def perform_create(self, serializer):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        serializer.save(shop_id=shop_id, tenant_id=shop_id)

    @action(detail=True, methods=['post'])
    def receive(self, request, pk=None):
        """Finalize a PO: ingest stock, update COGS, record the expense ledger entry."""
        shop_id = ShopDetailView()._resolve_shop_id(request)
        po = self.get_object()
        if po.status == PurchaseOrder.STATUS_CANCELLED:
            return Response(
                {'detail': 'A cancelled purchase order cannot be received.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            po = POService(shop_id).mark_as_received(str(po.id))
        except ProductVariant.DoesNotExist:
            return Response(
                {'detail': 'One or more line-item variants no longer exist.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(PurchaseOrderSerializer(po).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a PO. Only DRAFT/ORDERED orders can be cancelled."""
        po = self.get_object()
        if po.status not in (PurchaseOrder.STATUS_DRAFT, PurchaseOrder.STATUS_ORDERED):
            return Response(
                {'detail': f'A {po.status.lower()} purchase order cannot be cancelled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        po.status = PurchaseOrder.STATUS_CANCELLED
        po.save(update_fields=['status'])
        return Response(PurchaseOrderSerializer(po).data, status=status.HTTP_200_OK)


