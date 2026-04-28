from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.shortcuts import redirect
from django.conf import settings
from affiliates.models import AffiliateClick, Referral
from shops.models import Shop

class AffiliateViewSet(viewsets.ViewSet):
    permission_classes = [permissions.AllowAny]

    @action(detail=False, methods=['get'])
    def track(self, request):
        """
        Endpoint: /api/v1/affiliates/track/?ref=SUBDOMAIN
        Logs the click and redirects to signup with a tracking cookie.
        """
        ref_subdomain = request.query_params.get('ref')
        if not ref_subdomain:
            return redirect(f"{settings.WEB_URL}/signup")

        try:
            referrer_shop = Shop.objects.get(subdomain=ref_subdomain)
            
            # Log the click
            AffiliateClick.objects.create(
                referrer_shop=referrer_shop,
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT'),
                referer_url=request.META.get('HTTP_REFERER')
            )
            
            # Redirect to signup with ref in query param and set cookie
            response = redirect(f"{settings.WEB_URL}/signup?ref={ref_subdomain}")
            response.set_cookie('mohajon_ref', ref_subdomain, max_age=60*60*24*30) # 30 days
            return response
            
        except Shop.DoesNotExist:
            return redirect(f"{settings.WEB_URL}/signup")

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def stats(self, request):
        """
        Endpoint: /api/v1/affiliates/stats/
        Returns referral stats for the authenticated shop.
        """
        shop_id = getattr(request, 'tenant_id', None)
        if not shop_id:
            return Response({"error": "Shop context missing"}, status=status.HTTP_400_BAD_REQUEST)
            
        clicks_count = AffiliateClick.objects.filter(referrer_shop_id=shop_id).count()
        referrals = Referral.objects.filter(referrer_shop_id=shop_id)
        
        return Response({
            "total_clicks": clicks_count,
            "total_referrals": referrals.count(),
            "verified_referrals": referrals.filter(status='VERIFIED').count(),
            "total_reward": sum(r.reward_amount for r in referrals if r.status == 'VERIFIED'),
        })
