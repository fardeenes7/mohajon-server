from shops.models import Shop, ShopMember, StockLocation, LocationType
from django.db import transaction
from django.conf import settings
from django.core.exceptions import ValidationError
import re

def is_blacklisted(slug: str) -> bool:
    """
    Validates a subdomain against formatting rules, the explicit system blacklist,
    and checks if it currently exists.
    """
    if not re.match(r"^[a-z0-9-]+$", slug):
        return True
    
    if slug in getattr(settings, 'SUBDOMAIN_BLACKLIST', set()):
        return True
    
    return Shop.objects.filter(subdomain=slug).exists()

def shop_create(*, name: str, subdomain: str, owner_user) -> Shop:
    """
    Creates a new shop tenant and immediately assigns the creator as OWNER.
    Applies rigorous validation against system constraints.
    """
    slug = subdomain.lower().strip()
    
    if is_blacklisted(slug):
        raise ValidationError(f"The subdomain '{slug}' is invalid, blacklisted, or already claimed.")
        
    with transaction.atomic():
        shop = Shop.objects.create(name=name, subdomain=slug)
        
        # Create default location (Primary Branch)
        default_location = StockLocation.objects.create(
            shop=shop,
            name="Primary Branch",
            location_type=LocationType.BRANCH,
            is_default=True
        )

        ShopMember.objects.create(
            user=owner_user,
            shop=shop,
            role='OWNER',
            location=default_location
        )
    return shop
