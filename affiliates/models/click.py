from django.db import models
import uuid

class AffiliateClick(models.Model):
    """
    Tracks raw clicks on the 'Powered by Mohajon' link.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    referrer_shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE, related_name='affiliate_clicks')
    
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    referer_url = models.URLField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['referrer_shop', 'created_at']),
        ]
