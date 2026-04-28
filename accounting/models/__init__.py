import uuid
from django.db import models
from django.conf import settings
from core.models import SoftDeleteModel, TenantModel

class PlatformBalance(TenantModel):
    """
    Tracks the balance of a shop within the Mohajon platform.
    Used for settlements, shipping fee deductions, and markups.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.OneToOneField('shops.Shop', on_delete=models.CASCADE, related_name='platform_balance')
    
    total_earned = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_withdrawn = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    current_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    pending_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0) # T+7 hold

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

class LedgerEntry(TenantModel):
    """
    Single-entry ledger for all financial movements.
    """
    ENTRY_TYPE_INCOME = 'INCOME'
    ENTRY_TYPE_EXPENSE = 'EXPENSE'
    ENTRY_TYPE_PAYOUT = 'PAYOUT'
    ENTRY_TYPE_ADJUSTMENT = 'ADJUSTMENT'
    
    ENTRY_TYPES = [
        (ENTRY_TYPE_INCOME, 'Income'),
        (ENTRY_TYPE_EXPENSE, 'Expense'),
        (ENTRY_TYPE_PAYOUT, 'Payout'),
        (ENTRY_TYPE_ADJUSTMENT, 'Adjustment'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE, related_name='ledger_entries')
    
    entry_type = models.CharField(max_length=20, choices=ENTRY_TYPES)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    description = models.TextField()
    
    # Optional link to an order or payout
    order = models.ForeignKey('orders.Order', on_delete=models.SET_NULL, null=True, blank=True)
    payout = models.ForeignKey('accounting.Payout', on_delete=models.SET_NULL, null=True, blank=True)
    
    is_cleared = models.BooleanField(default=False, help_text="True if pending funds moved to current balance")
    cleared_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

class Payout(TenantModel):
    """
    Tracks merchant payouts.
    """
    STATUS_PENDING = 'PENDING'
    STATUS_PROCESSING = 'PROCESSING'
    STATUS_PAID = 'PAID'
    STATUS_FAILED = 'FAILED'
    STATUS_REVERSED = 'REVERSED'
    
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_PAID, 'Paid/Settled'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_REVERSED, 'Reversed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE, related_name='payouts')
    
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    
    bank_info = models.JSONField(help_text="Snapshot of merchant's bank/MFS info at time of payout")
    admin_note = models.TextField(blank=True)
    
    paid_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

class PurchaseOrder(TenantModel):
    """
    Tracks inventory purchases from suppliers.
    """
    STATUS_DRAFT = 'DRAFT'
    STATUS_ORDERED = 'ORDERED'
    STATUS_RECEIVED = 'RECEIVED'
    STATUS_CANCELLED = 'CANCELLED'
    
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_ORDERED, 'Ordered'),
        (STATUS_RECEIVED, 'Received'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE, related_name='purchase_orders')
    
    supplier_name = models.CharField(max_length=255)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2)
    auxiliary_costs = models.JSONField(default=dict, help_text="e.g. {'shipping': 500, 'customs': 2000}")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    
    # Simple JSON storage for MVP items: [{variant_id, quantity, unit_cost}]
    items_json = models.JSONField(default=list, blank=True)
    
    received_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

class Investor(TenantModel):
    """
    Tracks investor equity and profit splits.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE, related_name='investors')
    
    name = models.CharField(max_length=255)
    equity_percentage = models.DecimalField(max_digits=5, decimal_places=2) # e.g. 10.00 for 10%
    investment_amount = models.DecimalField(max_digits=15, decimal_places=2)
    
    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

class POSShift(TenantModel):
    """
    Manages daily POS shifts, cash reconciliation, and registers.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE, related_name='pos_shifts')
    cashier = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='pos_shifts')
    
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    
    opening_float = models.DecimalField(max_digits=15, decimal_places=2)
    expected_closing_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    actual_closing_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    
    reconciliation_notes = models.TextField(blank=True)
    
    is_reconciled = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)
