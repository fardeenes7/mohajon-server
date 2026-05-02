from django.db import models
import uuid
from core.models import SoftDeleteModel, TenantModel
from django.conf import settings
from .location import StockLocation, LocationType

class SubscriptionPlan(models.Model):
    """
    Defines the available tiers and their default limits.
    """
    TIER_CHOICES = (
        ('FREE', 'Free'),
        ('BASIC', 'Basic'),
        ('PRO', 'Pro'),
        ('BUSINESS', 'Business'),
    )
    name = models.CharField(max_length=20, choices=TIER_CHOICES, unique=True)
    max_products = models.IntegerField(default=5)
    max_staff = models.IntegerField(default=1)
    can_use_pos = models.BooleanField(default=False)
    can_use_api = models.BooleanField(default=False)
    can_use_pixels = models.BooleanField(default=False)  # Free plan cannot configure tracking pixels
    
    def __str__(self):
        return self.get_name_display()

class Shop(SoftDeleteModel):
    """
    Core Application Tenant.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    subdomain = models.CharField(max_length=100, unique=True, db_index=True)
    custom_domain = models.CharField(max_length=255, unique=True, null=True, blank=True, db_index=True)
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name='shops', null=True)
    grace_period_until = models.DateTimeField(null=True, blank=True)
    
    # Custom Overrides (from Business Rules limits)
    override_max_products = models.IntegerField(null=True, blank=True)
    override_max_staff = models.IntegerField(null=True, blank=True)
    is_billing_exempt = models.BooleanField(default=False)

    # Packaging weight added to all orders for courier tier calculation
    packaging_weight_grams = models.PositiveIntegerField(
        default=0,
        help_text="Added to every order's total weight for accurate courier billing",
    )

    # Fix 6.9 (post_v03_debrief.md): Canonical currency for all prices in this shop.
    # Individual price fields (base_price, price_override) inherit from this — they do
    # NOT carry their own currency metadata. Live exchange rate sync is out of scope.
    base_currency = models.CharField(
        max_length=3,
        default="BDT",
        help_text=(
            "ISO 4217 currency code for this shop. All price fields inherit "
            "from this value — individual prices do not carry their own currency. "
            "Example: BDT, USD, EUR."
        ),
    )

    def __str__(self):
        return self.name

class ShopMember(SoftDeleteModel):
    """
    Maps Users to Shops with Roles. Note: Does not inherit from TenantModel
    since it's a structural bridge, but it relates directly to the Tenant.
    """
    ROLE_CHOICES = (
        ('OWNER', 'Owner'),
        ('MANAGER', 'Manager'),
        ('INVENTORY_MANAGER', 'Inventory Manager'),
        ('CASHIER', 'Cashier'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shop_memberships')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='members')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='CASHIER')
    
    # Location Assignment (New)
    # Note: Owners are usually assigned to the default location but see all.
    # POS mode View Global Stock bypasses this restriction.
    location = models.ForeignKey(
        StockLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='members',
        help_text="Primary location this member works at."
    )
    
    class Meta:
        unique_together = ('user', 'shop')

    def __str__(self):
        return f"{self.user.email} - {self.shop.name} ({self.role})"


class ShopSettings(TenantModel):
    """
    Per-tenant configuration store for checkout, logistics, messaging, and
    operational controls.
    """

    TAX_CALCULATION_CHOICES = (
        ('ORIGINAL', 'Calculate Tax on Original Price'),
        ('DISCOUNTED', 'Calculate Tax on Discounted Price'),
    )
    
    DISCOUNT_APPLICATION_CHOICES = (
        ('EXCLUSIVE', 'Discount is Exclusive of Tax'),
        ('INCLUSIVE', 'Discount is Inclusive of Tax'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.OneToOneField(Shop, on_delete=models.CASCADE, related_name='settings')

    stock_reservation_minutes = models.PositiveIntegerField(default=30)
    allow_guest_checkout = models.BooleanField(default=True)
    mandatory_advance_fee_bdt = models.PositiveIntegerField(default=0)
    uses_platform_courier_credentials = models.BooleanField(default=False)
    prepaid_misuse_consecutive_limit = models.PositiveSmallIntegerField(default=5)
    maintenance_mode = models.BooleanField(default=False)
    branding_slug_release_days = models.PositiveIntegerField(default=30)

    ai_credit_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sms_enabled = models.BooleanField(default=False)
    sms_balance_credits = models.PositiveIntegerField(default=0)
    notification_targets = models.JSONField(default=list, blank=True)

    # Storefront Toggles
    show_stock_count = models.BooleanField(default=True)
    enable_product_reviews = models.BooleanField(default=True)
    tax_calculation_base = models.CharField(max_length=20, choices=TAX_CALCULATION_CHOICES, default='DISCOUNTED')
    discount_application = models.CharField(max_length=20, choices=DISCOUNT_APPLICATION_CHOICES, default='EXCLUSIVE')

    messenger_context_window_size = models.PositiveIntegerField(default=20)
    messenger_human_takeover_ttl_minutes = models.PositiveIntegerField(default=30)
    messenger_max_orders_per_psid_per_day = models.PositiveIntegerField(default=5)
    messenger_order_draft_ttl_hours = models.PositiveIntegerField(default=24)
    messenger_fallback_message = models.TextField(
        default="I'm having a little trouble right now. Our team will reach out to you shortly! 🙏"
    )
    messenger_greeting_keywords = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Case-insensitive full-message greeting keywords that trigger an auto-welcome "
            "without using AI credits. Leave empty to use the system defaults."
        ),
    )

    custom_domain_verified = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['shop', 'deleted_at'], name='shopsettings_shop_deleted_idx'),
        ]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Settings for {self.shop.name}"

class CustomerProfile(TenantModel):
    """
    End-Buyer identity mapped per Shop.
    Inherits from TenantModel to enforce RLS visibility per shop.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_number = models.CharField(max_length=20, db_index=True)
    name = models.CharField(max_length=255, blank=True)
    loyalty_points = models.IntegerField(default=0)
    
    def __str__(self):
        return f"{self.name} ({self.phone_number})"

class StoreTheme(TenantModel):
    """
    Multi-Theme Architecture data model for the storefront.
    Stores the selected base theme and any explicit aesthetic overrides.
    """
    THEME_CHOICES = (
        ('minimalist', 'Minimalist'),
        ('bold', 'Bold & Tech'),
        ('elegance', 'Soft Elegance'),
        ('urban', 'Urban Streetwear'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.OneToOneField(Shop, on_delete=models.CASCADE, related_name='theme')
    
    theme_id = models.CharField(max_length=50, choices=THEME_CHOICES, default='minimalist')
    
    # Stores overrides like primary color, corner radius (sharp/soft)
    aesthetic_overrides = models.JSONField(default=dict, blank=True)
    
    # Stores component selections (e.g., header: 'minimal', hero: 'split')
    active_components = models.JSONField(default=dict, blank=True)
    
    # Stores typography rules (heading font, body font)
    typography = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['shop', 'deleted_at'], name='storetheme_shop_deleted_idx'),
        ]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Theme ({self.theme_id}) for {self.shop.name}"
