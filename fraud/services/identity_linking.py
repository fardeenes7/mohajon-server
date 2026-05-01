from django.db import transaction
from django.utils import timezone

from fraud.models import IdentityLink, LinkReason
from users.models import Address, PhoneIdentity


def link_identity_on_phone_claim(phone_identity: PhoneIdentity, new_user) -> None:
    if (
        phone_identity.user_id == new_user.id
        and phone_identity.is_verified
        and phone_identity.last_verified_at
    ):
        return

    if not phone_identity.user_id or phone_identity.user_id == new_user.id:
        phone_identity.user = new_user
        phone_identity.is_verified = True
        phone_identity.last_verified_at = phone_identity.last_verified_at or timezone.now()
        phone_identity.save(update_fields=["user", "is_verified", "last_verified_at"])
        return

    with transaction.atomic():
        IdentityLink.objects.get_or_create(
            user_a=new_user,
            user_b_id=phone_identity.user_id,
            link_reason=LinkReason.SHARED_PHONE,
            defaults={"confidence_score": 1.0},
        )
        phone_identity.user = new_user
        phone_identity.is_verified = True
        phone_identity.last_verified_at = phone_identity.last_verified_at or timezone.now()
        phone_identity.save(update_fields=["user", "is_verified", "last_verified_at"])


def link_identity_on_address_create(address: Address) -> None:
    matches = Address.objects.filter(address_hash=address.address_hash).exclude(user_id=address.user_id)
    for match in matches.only("user_id"):
        IdentityLink.objects.get_or_create(
            user_a=address.user,
            user_b_id=match.user_id,
            link_reason=LinkReason.SHARED_ADDRESS,
            defaults={"confidence_score": 0.8},
        )
