from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PhoneIdentity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("phone_number", models.CharField(db_index=True, max_length=20, unique=True)),
                ("phone_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("phone_suffix", models.CharField(db_index=True, max_length=4)),
                ("is_verified", models.BooleanField(default=False)),
                ("trust_score", models.IntegerField(default=0)),
                ("last_verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="phone_identities",
                        to="users.user",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["phone_number"], name="phone_identity_phone_idx"),
                    models.Index(fields=["phone_suffix"], name="phone_identity_suffix_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Address",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("contact_name", models.CharField(max_length=255)),
                ("contact_phone", models.CharField(max_length=20)),
                ("street", models.TextField()),
                ("city", models.CharField(max_length=100)),
                ("postal_code", models.CharField(blank=True, max_length=20)),
                ("address_hash", models.CharField(db_index=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="addresses",
                        to="users.user",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user"], name="address_user_idx"),
                    models.Index(fields=["address_hash"], name="address_hash_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SocialAccount",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("provider", models.CharField(default="FACEBOOK", max_length=20)),
                ("provider_account_id", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="social_accounts",
                        to="users.user",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["provider", "provider_account_id"], name="social_provider_account_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="socialaccount",
            constraint=models.UniqueConstraint(fields=("user", "provider"), name="uq_user_provider"),
        ),
        migrations.AddConstraint(
            model_name="socialaccount",
            constraint=models.UniqueConstraint(fields=("provider", "provider_account_id"), name="uq_provider_account"),
        ),
    ]
