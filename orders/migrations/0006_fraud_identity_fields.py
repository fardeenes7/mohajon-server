from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_identity_models"),
        ("orders", "0005_order_payment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="orders",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="phone_identity",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="orders",
                to="users.phoneidentity",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_address",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="shipping_orders",
                to="users.address",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="billing_address",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="billing_orders",
                to="users.address",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="confidence_level",
            field=models.CharField(
                choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High")],
                default="LOW",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="is_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="order",
            name="verification_method",
            field=models.CharField(
                choices=[("OTP", "OTP Verification"), ("COURIER", "Courier Confirmation"), ("NONE", "None")],
                default="NONE",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="actor_reference",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["user", "created_at"], name="order_user_created_idx"),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["phone_identity", "created_at"], name="order_phone_created_idx"),
        ),
    ]
