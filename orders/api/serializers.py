from rest_framework import serializers

from orders.models import Order, OrderItem, PaymentInvoice
from shops.models import CustomerProfile



class PaymentInvoiceOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    variant_summary = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product_name",
            "variant_summary",
            "quantity",
            "unit_price",
            "line_discount_amount",
            "line_total_amount",
        ]

    def get_variant_summary(self, obj: OrderItem) -> str:
        if not obj.variant:
            return ""
        attrs = [obj.variant.attribute_value_1, obj.variant.attribute_value_2]
        return " / ".join([value for value in attrs if value])


class PaymentInvoiceOrderSummarySerializer(serializers.ModelSerializer):
    items = PaymentInvoiceOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "status",
            "subtotal_amount",
            "shipping_amount",
            "discount_amount",
            "total_amount",
            "currency",
            "items",
        ]


class PaymentInvoicePublicSerializer(serializers.ModelSerializer):
    order = PaymentInvoiceOrderSummarySerializer(read_only=True)

    class Meta:
        model = PaymentInvoice
        fields = [
            "token",
            "expires_at",
            "is_used",
            "order",
        ]


class PaymentInvoiceCodConfirmResponseSerializer(serializers.Serializer):
    order_id = serializers.UUIDField()
    order_status = serializers.CharField()
    invoice_token = serializers.UUIDField()


class CustomerShortSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        fields = ["id", "name", "phone_number"]


class OrderListSerializer(serializers.ModelSerializer):
    customer = CustomerShortSerializer(source="customer_profile", read_only=True)
    item_count = serializers.IntegerField(source="items.count", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "status",
            "total_amount",
            "currency",
            "customer",
            "item_count",
            "created_at",
            "updated_at",
        ]


class OrderDetailSerializer(serializers.ModelSerializer):
    customer = CustomerShortSerializer(source="customer_profile", read_only=True)
    items = PaymentInvoiceOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "status",
            "total_amount",
            "subtotal_amount",
            "shipping_amount",
            "discount_amount",
            "currency",
            "customer",
            "shipping_address",
            "billing_address",
            "items",
            "created_at",
            "updated_at",
        ]


class OrderStatusTransitionSerializer(serializers.Serializer):
    to_status = serializers.CharField()
    reason = serializers.CharField(required=False, allow_blank=True)

