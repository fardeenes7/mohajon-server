from rest_framework import viewsets, status, views
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from shops.api.views import ShopDetailView
from shipping.models import CourierAccount, CourierConsignment
from shipping.api.serializers import CourierAccountSerializer, CourierConsignmentSerializer
from shipping.services import (
    set_courier_credentials,
    create_shipment,
    estimate_shipping_price,
    get_courier_locations,
    get_shipment_tracking,
)

class CourierAccountViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CourierAccountSerializer

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        return CourierAccount.objects.filter(shop_id=shop_id, deleted_at__isnull=True)

    @action(detail=False, methods=['post'], url_path='configure')
    def configure(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        provider = request.data.get('provider')
        credentials = request.data.get('credentials')
        is_test_mode = request.data.get('is_test_mode', False)
        label = request.data.get('label', '')
        default_store_id = request.data.get('default_store_id', '')

        if not provider or not credentials:
            return Response(
                {"error": "Provider and credentials are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        account = set_courier_credentials(
            shop_id=shop_id,
            provider=provider,
            credentials=credentials,
            is_test_mode=is_test_mode,
            label=label,
            default_store_id=default_store_id,
        )
        return Response(self.get_serializer(account).data)


class CourierLocationView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        provider = request.query_params.get('provider')
        if not provider:
            return Response(
                {"error": "Provider query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        city_id = request.query_params.get('city_id')
        zone_id = request.query_params.get('zone_id')

        try:
            parsed_city_id = int(city_id) if city_id else None
            parsed_zone_id = int(zone_id) if zone_id else None
        except ValueError:
            return Response(
                {"error": "city_id and zone_id must be integers"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            locations = get_courier_locations(
                shop_id=shop_id,
                provider=provider,
                city_id=parsed_city_id,
                zone_id=parsed_zone_id,
            )
            return Response(locations)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CourierPriceEstimateView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        provider = request.data.get('provider')
        price_request = request.data.get('price_request')

        if not provider or not price_request:
            return Response(
                {"error": "provider and price_request are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            estimate = estimate_shipping_price(
                shop_id=shop_id,
                provider=provider,
                price_request=price_request,
            )
            return Response(estimate)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CourierConsignmentViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CourierConsignmentSerializer

    def get_queryset(self):
        shop_id = ShopDetailView()._resolve_shop_id(self.request)
        return CourierConsignment.objects.filter(shop_id=shop_id).order_by('-created_at')

    @action(detail=False, methods=['post'], url_path='create')
    def create_shipment_api(self, request):
        order_id = request.data.get('order_id')
        provider = request.data.get('provider')

        if not order_id or not provider:
            return Response(
                {"error": "order_id and provider are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            consignment = create_shipment(
                order_id=order_id,
                provider=provider,
                actor_user_id=str(request.user.id),
            )
            return Response(self.get_serializer(consignment).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='tracking')
    def tracking(self, request, pk=None):
        try:
            consignment = self.get_queryset().get(pk=pk)
        except CourierConsignment.DoesNotExist:
            return Response({"error": "Consignment not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            tracking_info = get_shipment_tracking(order_id=str(consignment.order_id))
            if not tracking_info:
                return Response(
                    {
                        "status": consignment.status,
                        "tracking_code": consignment.tracking_code,
                        "payload": consignment.payload,
                    }
                )
            return Response(tracking_info)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
