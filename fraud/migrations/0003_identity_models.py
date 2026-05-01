from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_identity_models"),
        ("orders", "0006_fraud_identity_fields"),
        ("fraud", "0002_globalfraudpool"),
    ]

    operations = [
        migrations.CreateModel(
            name="FraudProfile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("target_type", models.CharField(choices=[("PHONE", "Phone"), ("USER", "User"), ("FACEBOOK", "Facebook"), ("IP", "IP Address")], max_length=20)),
                ("target_id", models.CharField(max_length=255)),
                ("risk_score", models.IntegerField(db_index=True, default=0)),
                ("risk_level", models.CharField(choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High")], default="LOW", max_length=10)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "phone_identity",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="fraud_profiles",
                        to="users.phoneidentity",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["target_type", "target_id"], name="fraud_target_idx"),
                    models.Index(fields=["risk_score"], name="fraud_risk_score_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="FraudEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(choices=[("ORDER_CREATED", "Order Created"), ("ORDER_CANCELLED", "Order Cancelled"), ("DELIVERY_FAILED", "Delivery Failed"), ("FRAUD_REPORT_ADDED", "Fraud Report Added")], max_length=30)),
                ("score_impact", models.IntegerField(default=0)),
                ("confidence_level", models.CharField(max_length=10)),
                ("source_type", models.CharField(choices=[("ACTOR", "Actor"), ("PHONE", "Phone")], max_length=10)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="fraud_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "fraud_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="fraud.fraudprofile",
                    ),
                ),
                (
                    "phone_identity",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="fraud_events",
                        to="users.phoneidentity",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["fraud_profile", "created_at"], name="fraud_event_profile_time_idx"),
                    models.Index(fields=["actor", "created_at"], name="fraud_event_actor_time_idx"),
                    models.Index(fields=["phone_identity", "created_at"], name="fraud_event_phone_time_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="IdentityLink",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("link_reason", models.CharField(max_length=30)),
                ("confidence_score", models.FloatField(default=1.0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user_a",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="identity_links_a",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user_b",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="identity_links_b",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user_a", "user_b"], name="identity_link_users_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="fraudprofile",
            constraint=models.UniqueConstraint(fields=("target_type", "target_id"), name="uq_fraud_target"),
        ),
        migrations.AddConstraint(
            model_name="identitylink",
            constraint=models.UniqueConstraint(fields=("user_a", "user_b", "link_reason"), name="uq_identity_link"),
        ),
        migrations.AddField(
            model_name="fraudreport",
            name="merchant_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="fraudreport",
            name="order",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="fraud_reports", to="orders.order"),
        ),
        migrations.AddField(
            model_name="fraudreport",
            name="phone_identity",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="users.phoneidentity"),
        ),
        migrations.AddField(
            model_name="fraudreport",
            name="weight",
            field=models.IntegerField(default=10),
        ),
        migrations.AddIndex(
            model_name="fraudreport",
            index=models.Index(fields=["reported_at"], name="fraud_reported_at_idx"),
        ),
    ]
