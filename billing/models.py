"""
Billing, Subscription, and Payment Gateway models for Mohajon.

EPIC A — Subscription & Access Control
EPIC B — Payment Gateways & Processing
EPIC G — Developer API Tokens & Outbound Webhooks
"""

import uuid
import secrets
import hashlib

from django.db import models
from django.utils import timezone
from django.conf import settings

from core.models import SoftDeleteModel, TenantModel


# ─── EPIC A: Subscription & Billing State ─────────────────────────────────────

class ShopSubscription(TenantModel):
    """
    Tracks the current billing state for a Shop.

    This is the source-of-truth for the plan lifecycle:
      ACTIVE → GRACE (payment failed) → SUSPENDED (7-day grace elapsed)
      ACTIVE → COMPLIANCE_LOCK (downgrade over-quota)
      ACTIVE → MAINTENANCE (merchant-initiated)

    The FeatureGate service reads this model to gate access.
    """

    STATUS_ACTIVE = 'ACTIVE'
    STATUS_GRACE = 'GRACE'          # Payment failed, within 7-day grace period
    STATUS_SUSPENDED = 'SUSPENDED'  # Grace elapsed — storefront paused, dashboard read-only
    STATUS_COMPLIANCE_LOCK = 'COMPLIANCE_LOCK'  # Downgrade over-quota
    STATUS_CANCELLED = 'CANCELLED'

    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_GRACE, 'Grace Period'),
        (STATUS_SUSPENDED, 'Suspended'),
        (STATUS_COMPLIANCE_LOCK, 'Compliance Lock'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    TIER_FREE = 'FREE'
    TIER_BASIC = 'BASIC'
    TIER_PRO = 'PRO'
    TIER_BUSINESS = 'BUSINESS'
    TIER_CUSTOM = 'CUSTOM'

    TIER_CHOICES = [
        (TIER_FREE, 'Free'),
        (TIER_BASIC, 'Basic — 990 ৳/mo'),
        (TIER_PRO, 'Pro — 1,990 ৳/mo'),
        (TIER_BUSINESS, 'Business — 4,990 ৳/mo'),
        (TIER_CUSTOM, 'Custom / Staff Plan'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.OneToOneField(
        'shops.Shop',
        on_delete=models.CASCADE,
        related_name='subscription',
    )

    # Current plan tier
    tier = models.CharField(max_length=20, choices=TIER_CHOICES, default=TIER_FREE)

    # Billing lifecycle state
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    # When the current billing cycle started
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)

    # Grace period end — set to now()+7days on first payment failure
    grace_period_until = models.DateTimeField(
        null=True, blank=True,
        help_text="If set, shop is in grace period until this datetime. After this, status → SUSPENDED.",
    )

    # Date of the last successful payment
    last_paid_at = models.DateTimeField(null=True, blank=True)

    # Internal flag: bypass all billing checks (staff/friends accounts)
    is_billing_exempt = models.BooleanField(
        default=False,
        help_text="Admin-only. Completely skips billing enforcement for test/staff shops.",
    )

    # Custom pricing note for internal admin reference
    admin_note = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=['shop'],
                condition=models.Q(deleted_at__isnull=True),
                name='billing_sub_shop_active_idx',
            ),
            models.Index(
                fields=['status'],
                condition=models.Q(deleted_at__isnull=True),
                name='billing_sub_status_active_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.shop.name} — {self.get_tier_display()} [{self.get_status_display()}]"

    # ── Convenience helpers ────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.status in (self.STATUS_ACTIVE, self.STATUS_GRACE)

    @property
    def is_in_grace(self) -> bool:
        if self.status != self.STATUS_GRACE:
            return False
        return self.grace_period_until is not None and timezone.now() < self.grace_period_until

    @property
    def is_storefront_live(self) -> bool:
        """True when the customer-facing storefront is accessible."""
        return self.status == self.STATUS_ACTIVE or self.is_in_grace

    @property
    def suspension_banner(self) -> str | None:
        """
        Returns the exact storefront banner copy per plan doc rules:
          - GRACE/SUSPENDED  → '⚠️ Service Paused — Payment Issue'
          - COMPLIANCE_LOCK  → '🔒 Account Under Review'
          - (maintenance_mode in ShopSettings) → '🔧 Undergoing Maintenance'
        """
        match self.status:
            case self.STATUS_GRACE | self.STATUS_SUSPENDED:
                return "⚠️ Service Paused — Payment Issue"
            case self.STATUS_COMPLIANCE_LOCK:
                return "🔒 Account Under Review"
            case _:
                return None


# ─── EPIC B: Payment Gateway Configuration ────────────────────────────────────

class PaymentGatewayConfig(TenantModel):
    """
    Stores encrypted merchant API credentials for payment gateways.

    Keys are encrypted at-rest via django-encrypted-model-fields.
    The 30-minute gateway key memory rule is enforced in Redis by the
    checkout service layer — not here.
    """

    GATEWAY_BKASH = 'BKASH'
    GATEWAY_SSLCOMMERZ = 'SSLCOMMERZ'
    GATEWAY_PORTPOS = 'PORTPOS'
    GATEWAY_MANUAL = 'MANUAL'

    GATEWAY_CHOICES = [
        (GATEWAY_BKASH, 'bKash (Tokenized)'),
        (GATEWAY_SSLCOMMERZ, 'SSLCommerz'),
        (GATEWAY_PORTPOS, 'PortPOS'),
        (GATEWAY_MANUAL, 'Manual / Bank Transfer'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        'shops.Shop',
        on_delete=models.CASCADE,
        related_name='payment_gateways',
    )
    gateway = models.CharField(max_length=20, choices=GATEWAY_CHOICES)

    # Encrypted credential blobs (JSON-serialized key-value pairs)
    # Stored as plain text in dev; replace with EncryptedTextField in prod via
    # django-encrypted-model-fields or django-fernet-fields.
    # TODO: Swap to EncryptedTextField before first production deployment.
    credentials_encrypted = models.TextField(
        blank=True,
        help_text=(
            "JSON-encoded gateway credentials stored encrypted. "
            "Use billing.services.gateway.set_gateway_credentials() to write — never write directly."
        ),
    )

    # Human-readable label for this config (e.g. 'My bKash Business Account')
    label = models.CharField(max_length=100, blank=True)

    is_active = models.BooleanField(
        default=True,
        help_text="Merchant can disable a gateway without deleting its credentials.",
    )
    is_test_mode = models.BooleanField(
        default=False,
        help_text="Toggles the gateway SDK to its sandbox environment.",
    )

    class Meta:
        unique_together = ('shop', 'gateway')
        indexes = [
            models.Index(
                fields=['shop', 'is_active'],
                condition=models.Q(deleted_at__isnull=True),
                name='billing_gw_shop_active_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.shop.name} — {self.get_gateway_display()}"


class PaymentMethod(TenantModel):
    """
    Per-shop toggle for each payment method visible to customers at checkout.

    Merchants can enable/disable COD, bKash, SSLCommerz, bank transfer, etc.
    at any time. A gateway must also have an active PaymentGatewayConfig to
    function — disabling here hides it from the storefront without removing creds.
    """

    METHOD_COD = 'COD'
    METHOD_BKASH = 'BKASH'
    METHOD_SSLCOMMERZ = 'SSLCOMMERZ'
    METHOD_PORTPOS = 'PORTPOS'
    METHOD_BANK_TRANSFER = 'BANK_TRANSFER'
    METHOD_CUSTOM = 'CUSTOM'

    METHOD_CHOICES = [
        (METHOD_COD, 'Cash on Delivery'),
        (METHOD_BKASH, 'bKash'),
        (METHOD_SSLCOMMERZ, 'SSLCommerz / Card'),
        (METHOD_PORTPOS, 'PortPOS'),
        (METHOD_BANK_TRANSFER, 'Bank Transfer'),
        (METHOD_CUSTOM, 'Custom Manual Payment'),
    ]

    # Who absorbs the gateway fee — merchant or customer
    FEE_PAYER_MERCHANT = 'MERCHANT'
    FEE_PAYER_CUSTOMER = 'CUSTOMER'
    FEE_PAYER_CHOICES = [
        (FEE_PAYER_MERCHANT, 'Merchant absorbs fee'),
        (FEE_PAYER_CUSTOMER, 'Customer pays fee'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        'shops.Shop',
        on_delete=models.CASCADE,
        related_name='payment_methods',
    )
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    is_enabled = models.BooleanField(default=True)

    # Display order on the checkout screen (lower = first)
    display_order = models.PositiveSmallIntegerField(default=0)

    # Gateway fee pass-through preference per business rules §3
    fee_payer = models.CharField(
        max_length=10,
        choices=FEE_PAYER_CHOICES,
        default=FEE_PAYER_MERCHANT,
        help_text="Defaults to merchant per global_business_rules_and_limits.md §3.",
    )

    # For METHOD_CUSTOM: instructions the customer must follow
    custom_instructions = models.TextField(
        blank=True,
        help_text="e.g. 'Send to bKash 01XXXXXXXXX and paste your TxnID below.'",
    )
    # For METHOD_CUSTOM: require a transaction ID field in the storefront form
    requires_transaction_id = models.BooleanField(default=False)

    class Meta:
        unique_together = ('shop', 'method')
        ordering = ['display_order']
        indexes = [
            models.Index(
                fields=['shop', 'is_enabled'],
                condition=models.Q(deleted_at__isnull=True),
                name='billing_pm_shop_enabled_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

    def __str__(self):
        status = "✓" if self.is_enabled else "✗"
        return f"{status} {self.shop.name} — {self.get_method_display()}"

class BKashAgreement(TenantModel):
    """
    Stores bKash Agreement details for a customer.
    Required for Tokenized Checkout.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE)
    customer_identifier = models.CharField(max_length=100, help_text="e.g. customer phone or user id")
    
    agreement_id = models.CharField(max_length=255, unique=True)
    payer_reference = models.CharField(max_length=100)
    
    status = models.CharField(max_length=20, default='ACTIVE')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

class PaymentTransaction(TenantModel):
    """
    General ledger for all payment attempts.
    """
    STATUS_INITIATED = 'INITIATED'
    STATUS_AUTHORIZED = 'AUTHORIZED'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_FAILED = 'FAILED'
    STATUS_REFUNDED = 'REFUNDED'
    STATUS_PARTIALLY_REFUNDED = 'PARTIALLY_REFUNDED'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE)
    
    # Generic linkage
    order_id = models.UUIDField(null=True, blank=True, help_text="Storefront order UUID")
    subscription_id = models.UUIDField(null=True, blank=True, help_text="ShopSubscription UUID")
    
    gateway = models.CharField(max_length=20, choices=PaymentGatewayConfig.GATEWAY_CHOICES)
    external_transaction_id = models.CharField(max_length=255, blank=True, db_index=True)
    
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default='BDT')
    
    status = models.CharField(max_length=20, default=STATUS_INITIATED)
    
    gateway_response = models.JSONField(default=dict, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

class RefundRecord(TenantModel):
    """
    Tracks partial refunds for a transaction.
    Tokenized bKash allows up to 10.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE)
    transaction = models.ForeignKey(PaymentTransaction, on_delete=models.CASCADE, related_name='refunds')
    
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    reason = models.TextField(blank=True)
    
    external_refund_id = models.CharField(max_length=255, blank=True)
    gateway_response = models.JSONField(default=dict, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)


# ─── EPIC B: AI Credits (v0.9) ────────────────────────────────────────────────

class AICreditPackage(models.Model):
    """
    Fixed-price credit bundles available for purchase.
    Profit margin is built into the retail_price_bdt.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)  # e.g. "Starter Pack (100 Credits)"
    credits = models.PositiveIntegerField()
    retail_price_bdt = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "credits"]

    def __str__(self) -> str:
        return f"{self.name} | {self.retail_price_bdt} ৳"


class AICreditTopUp(TenantModel):
    """
    Tracks a specific purchase of AI credits by a shop.
    Linked to a PaymentTransaction once completed.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="ai_topups",
    )
    package = models.ForeignKey(AICreditPackage, on_delete=models.SET_NULL, null=True)

    credits_purchased = models.PositiveIntegerField()
    amount_paid_bdt = models.DecimalField(max_digits=10, decimal_places=2)

    # Link to the transaction that funded this top-up
    transaction = models.OneToOneField(
        "billing.PaymentTransaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_topup_record",
    )

    status = models.CharField(max_length=20, default="PENDING")  # PENDING, COMPLETED, FAILED

    # Credits expire in 60 days per business rules §3
    expires_at = models.DateTimeField(null=True, blank=True)
    is_expired = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.shop_id} | {self.credits_purchased} credits | {self.status}"



# ─── EPIC G: Developer API Tokens ─────────────────────────────────────────────

class MerchantAPIToken(TenantModel):
    """
    Revocable, scoped API tokens for Business-plan merchants.

    Token format: nsc_{32-char hex}
    The raw token is only returned on creation — only the SHA-256 hash
    is stored to prevent leakage if the DB is compromised.
    """

    SCOPE_READ_ORDERS = 'orders:read'
    SCOPE_WRITE_ORDERS = 'orders:write'
    SCOPE_READ_CATALOG = 'catalog:read'
    SCOPE_WRITE_CATALOG = 'catalog:write'
    SCOPE_READ_INVENTORY = 'inventory:read'
    SCOPE_WRITE_INVENTORY = 'inventory:write'

    ALL_SCOPES = [
        SCOPE_READ_ORDERS,
        SCOPE_WRITE_ORDERS,
        SCOPE_READ_CATALOG,
        SCOPE_WRITE_CATALOG,
        SCOPE_READ_INVENTORY,
        SCOPE_WRITE_INVENTORY,
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        'shops.Shop',
        on_delete=models.CASCADE,
        related_name='api_tokens',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_api_tokens',
    )

    # Human label for the token (e.g. "Inventory Sync Bot")
    name = models.CharField(max_length=100)

    # SHA-256 hex digest of the raw token — the raw token is never stored
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)

    # Token prefix for identification without revealing the secret (e.g., "nsc_4a3b...")
    token_prefix = models.CharField(max_length=12)

    # JSON list of granted scope strings
    scopes = models.JSONField(default=list)

    # Optional expiry (null = non-expiring until explicitly revoked)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=['shop'],
                condition=models.Q(deleted_at__isnull=True),
                name='billing_apitoken_shop_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.token_prefix}…) — {self.shop.name}"

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return timezone.now() > self.expires_at

    @classmethod
    def generate(cls, shop, created_by, name: str, scopes: list[str], expires_at=None):
        """
        Factory: generates a raw token, hashes it, stores the hash.
        Returns (instance, raw_token). Raw token is shown ONCE — not re-retrievable.
        """
        raw_token = f"nsc_{secrets.token_hex(32)}"
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_prefix = raw_token[:10]   # "nsc_XXXXXX"

        instance = cls(
            shop=shop,
            created_by=created_by,
            name=name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            scopes=scopes,
            expires_at=expires_at,
        )
        instance.save()
        return instance, raw_token

    @classmethod
    def authenticate(cls, raw_token: str):
        """
        Looks up and validates a raw token. Returns the instance or None.
        Also enforces expiry and updates last_used_at.
        """
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        try:
            token = cls.objects.select_related('shop').get(
                token_hash=token_hash,
                deleted_at__isnull=True,
            )
        except cls.DoesNotExist:
            return None

        if token.is_expired:
            return None

        # Touch last_used_at (non-blocking best-effort)
        cls.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        return token


# ─── EPIC G: Outbound Webhooks ────────────────────────────────────────────────

class OutboundWebhook(TenantModel):
    """
    Merchant-configured webhook endpoints.

    Dispatched via CloudEvents standard with HMAC-SHA256 payload signatures.
    Scoped to specific event types. Business-plan feature only.
    """

    # CloudEvents-compatible event types
    EVENT_ORDER_CREATED = 'order.created'
    EVENT_ORDER_UPDATED = 'order.updated'
    EVENT_ORDER_CANCELLED = 'order.cancelled'
    EVENT_PAYMENT_RECEIVED = 'payment.received'
    EVENT_PRODUCT_UPDATED = 'product.updated'
    EVENT_INVENTORY_LOW = 'inventory.low_stock'

    ALL_EVENTS = [
        EVENT_ORDER_CREATED,
        EVENT_ORDER_UPDATED,
        EVENT_ORDER_CANCELLED,
        EVENT_PAYMENT_RECEIVED,
        EVENT_PRODUCT_UPDATED,
        EVENT_INVENTORY_LOW,
    ]

    STATUS_ACTIVE = 'ACTIVE'
    STATUS_PAUSED = 'PAUSED'
    STATUS_FAILED = 'FAILED'   # Auto-paused after consecutive failures

    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_PAUSED, 'Paused'),
        (STATUS_FAILED, 'Disabled (Repeated Failures)'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        'shops.Shop',
        on_delete=models.CASCADE,
        related_name='outbound_webhooks',
    )

    url = models.URLField(max_length=500, help_text="HTTPS endpoint that receives events.")
    secret = models.CharField(
        max_length=64,
        help_text="HMAC-SHA256 signing secret. Shown once on creation.",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    # List of subscribed event type strings from ALL_EVENTS
    subscribed_events = models.JSONField(default=list)

    # Delivery stats
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=['shop', 'status'],
                condition=models.Q(deleted_at__isnull=True),
                name='billing_wh_shop_status_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        # Auto-generate a signing secret if not set
        if not self.secret:
            self.secret = secrets.token_hex(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.shop.name} → {self.url} [{self.status}]"

    def build_signature(self, payload_bytes: bytes) -> str:
        """
        Returns X-Mohajon-Signature header value.
        Format: sha256=<hex_digest>
        """
        import hmac
        digest = hmac.new(
            self.secret.encode(),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={digest}"
