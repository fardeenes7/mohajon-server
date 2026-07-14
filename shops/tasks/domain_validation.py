from celery import shared_task
import logging
from django.utils import timezone
from shops.models import Shop, ShopSettings
from shops.services.dns_health import verify_dns_readiness
import redis
from django.conf import settings

logger = logging.getLogger(__name__)

def update_traefik_dynamic_config(shop_subdomain: str, custom_domain: str):
    """
    Updates the Traefik dynamic configuration.
    Traefik can read from a Redis KV store to dynamically add routers for the new domain.
    """
    try:
        # Example Redis-based Traefik config update
        # Key structure: traefik/http/routers/{shop_subdomain}-custom/rule
        redis_client = redis.Redis.from_url(settings.REDIS_URL)
        
        router_key = f"traefik/http/routers/{shop_subdomain}-custom"
        
        # Set the rule
        rule = f"Host(`{custom_domain}`)"
        redis_client.set(f"{router_key}/rule", rule)
        
        # Set TLS certresolver
        redis_client.set(f"{router_key}/tls/certresolver", "letsencrypt")
        
        # Point to the existing storefront service
        redis_client.set(f"{router_key}/service", "storefront-svc")
        
        logger.info(f"Successfully updated Traefik routing for {custom_domain}")
    except Exception as e:
        logger.error(f"Failed to update Traefik config for {custom_domain}: {str(e)}")

@shared_task
def validate_pending_custom_domains():
    """
    Periodic task to check all shops that have a custom domain but are not yet verified.
    """
    unverified_shops = Shop.objects.filter(
        custom_domain__isnull=False,
        settings__custom_domain_verified=False
    ).select_related('settings')

    for shop in unverified_shops:
        domain = shop.custom_domain
        logger.info(f"Checking DNS readiness for {domain}")
        
        result = verify_dns_readiness(domain)
        
        if result["valid"]:
            # State Machine transition: Pending -> Verified
            shop_settings = shop.settings
            shop_settings.custom_domain_verified = True
            shop_settings.save(update_fields=['custom_domain_verified'])
            
            # Now safe to update Traefik to request SSL
            update_traefik_dynamic_config(shop.subdomain, domain)
            
            # Optional: trigger notification to merchant
            logger.info(f"Domain {domain} successfully verified and activated.")
        else:
            logger.info(f"Domain {domain} not ready: {result['reason']}")
