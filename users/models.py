import hashlib
import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import SoftDeleteModel

class UserManager(BaseUserManager):
    """Define a model manager for User model with no username field."""
    
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        """Create and save a User with the given email and password."""
        if not email:
            raise ValueError('The given email must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        """Create and save a regular User with the given email and password."""
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        """Create and save a SuperUser with the given email and password."""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self._create_user(email, password, **extra_fields)

class User(AbstractUser, SoftDeleteModel):
    """
    Centralized Global User model.
    Uses email as the unique identifier instead of username.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None # Remove username field
    email = models.EmailField(_('email address'), unique=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


def normalize_phone(phone_number: str) -> str:
    return "".join(filter(str.isdigit, phone_number or ""))


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PhoneIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="phone_identities")
    phone_number = models.CharField(max_length=20, unique=True, db_index=True)
    phone_hash = models.CharField(max_length=64, unique=True, db_index=True)
    phone_suffix = models.CharField(max_length=4, db_index=True)
    is_verified = models.BooleanField(default=False)
    trust_score = models.IntegerField(default=0)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["phone_number"], name="phone_identity_phone_idx"),
            models.Index(fields=["phone_suffix"], name="phone_identity_suffix_idx"),
        ]

    def save(self, *args, **kwargs):
        normalized = normalize_phone(self.phone_number)
        self.phone_number = normalized
        self.phone_hash = hash_text(normalized)
        self.phone_suffix = normalized[-4:] if len(normalized) >= 4 else normalized
        super().save(*args, **kwargs)


class Address(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    contact_name = models.CharField(max_length=255)
    contact_phone = models.CharField(max_length=20)
    street = models.TextField()
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20, blank=True)
    address_hash = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user"], name="address_user_idx"),
            models.Index(fields=["address_hash"], name="address_hash_idx"),
        ]

    def save(self, *args, **kwargs):
        normalized_addr = f"{self.street.lower().strip()}|{self.city.lower().strip()}|{self.postal_code.lower().strip()}"
        self.address_hash = hash_text(normalized_addr)
        super().save(*args, **kwargs)


class SocialAccount(models.Model):
    PROVIDER_FACEBOOK = "FACEBOOK"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="social_accounts")
    provider = models.CharField(max_length=20, default=PROVIDER_FACEBOOK)
    provider_account_id = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "provider"], name="uq_user_provider"),
            models.UniqueConstraint(fields=["provider", "provider_account_id"], name="uq_provider_account"),
        ]
        indexes = [
            models.Index(fields=["provider", "provider_account_id"], name="social_provider_account_idx"),
        ]
