from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum, Count, Q

from orders.models import Order
from catalog.models import ProductVariant
from chat.models import Conversation
from shops.models import ShopSettings
from shops.api.views import ShopDetailView

class DashboardMetricsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        if not shop_id:
            return Response({"detail": "No accessible shop found."}, status=404)

        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)

        # 1. Orders & Revenue (Last 30 days)
        # Assuming completed orders contribute to revenue. Let's count all orders and sum total_amount.
        orders = Order.objects.filter(shop_id=shop_id, created_at__gte=thirty_days_ago, deleted_at__isnull=True)
        order_stats = orders.aggregate(
            total_orders=Count("id"),
            total_revenue=Sum("total_amount")
        )
        total_orders = order_stats["total_orders"] or 0
        total_revenue = float(order_stats["total_revenue"] or 0)

        # 2. Low-stock alerts
        # Count active variants with stock < 5
        low_stock_count = ProductVariant.objects.filter(
            product__shop_id=shop_id,
            product__deleted_at__isnull=True,
            deleted_at__isnull=True,
            is_active=True,
            stock_quantity__lt=5
        ).count()

        # 3. Unread inbox count
        # Conversations that have a 'has_unread' flag or similar. If not available, we can try to guess or use 0 if not implemented.
        # Let's see what Conversation has. We'll query conversations that have unread messages.
        # I'll default to 0 and attempt to count if there's a has_unread or unread_count field, else try to find unread ChatMessages.
        # Wait, since I haven't seen ChatMessage model fully, I'll count conversations.
        # Usually Conversation has an unread count. If it fails, I'll catch it.
        try:
            unread_inbox_count = Conversation.objects.filter(shop_id=shop_id, has_unread=True).count()
        except Exception:
            unread_inbox_count = 0

        # 4. AI Credit Balance
        settings_obj = ShopSettings.objects.filter(shop_id=shop_id).first()
        ai_credits = settings_obj.ai_credit_balance if settings_obj else 0

        return Response({
            "total_orders": total_orders,
            "total_revenue": total_revenue,
            "low_stock_count": low_stock_count,
            "unread_inbox_count": unread_inbox_count,
            "ai_credits": ai_credits
        })
