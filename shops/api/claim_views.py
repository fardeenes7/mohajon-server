from rest_framework import generics, status, serializers
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from shops.models import Shop, SubscriptionPlan, ShopMember
from billing.models import ShopSubscription
from affiliates.models import Referral
from django.db import transaction
from django.conf import settings



class ShopCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    subdomain = serializers.CharField(max_length=100)
    ref = serializers.CharField(max_length=100, required=False, allow_null=True)

class ShopCreateView(generics.CreateAPIView):
    """
    Publicly accessible store creation for authenticated users.
    Waitlist system is removed.
    """
    permission_classes = [IsAuthenticated] # Must be logged in via Google
    serializer_class = ShopCreateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        subdomain = serializer.validated_data['subdomain'].lower()
        name = serializer.validated_data['name']
        ref_subdomain = serializer.validated_data.get('ref') or request.COOKIES.get('mohajon_ref')
        user = request.user

        # 1. Check if user already owns a shop (Mohajon is 1-shop-per-user for now)
        if ShopMember.objects.filter(user=user, role='OWNER').exists():
            return Response({'detail': 'You already own a shop.'}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Check subdomain availability
        if Shop.objects.filter(subdomain=subdomain).exists():
            return Response({'detail': 'Subdomain already taken.'}, status=status.HTTP_400_BAD_REQUEST)

        if subdomain in settings.SUBDOMAIN_BLACKLIST:
             return Response({'detail': 'Subdomain is reserved.'}, status=status.HTTP_400_BAD_REQUEST)

        referrer_shop = None
        if ref_subdomain:
            referrer_shop = Shop.objects.filter(subdomain=ref_subdomain).first()

        with transaction.atomic():
            # 3. Assign Default Free Plan
            free_plan, _ = SubscriptionPlan.objects.get_or_create(name='FREE')
            
            # 4. Create Shop
            shop = Shop.objects.create(
                name=name,
                subdomain=subdomain,
                plan=free_plan
            )
            
            # 5. Map Owner
            ShopMember.objects.create(
                user=user,
                shop=shop,
                role='OWNER'
            )

            # 6. Bootstrap billing — create ShopSubscription row eagerly so that
            #    get_subscription_context() never hits a missing-row edge case.
            #    get_or_create_subscription() would lazily create this, but doing it
            #    here keeps all shop-creation side-effects in one atomic block.
            ShopSubscription.objects.create(
                shop=shop,
                tenant_id=shop.id,
                tier=ShopSubscription.TIER_FREE,
                status=ShopSubscription.STATUS_ACTIVE,
            )

            # 7. Track Referral
            if referrer_shop:
                Referral.objects.create(
                    referrer_shop=referrer_shop,
                    referred_shop=shop,
                    status='PENDING'
                )
            
        return Response({
            'detail': 'Shop created successfully!', 
            'subdomain': subdomain,
            'shop_id': str(shop.id)
        }, status=status.HTTP_201_CREATED)

