import hmac
from hashlib import sha256

from django.test import TestCase

from shops.models import Shop
from webhooks.models import WebhookProcessingStatus, WebhookProvider
from webhooks.services import webhook_log_event, webhook_signature_valid


class WebhookServicesTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(name="Demo", subdomain="demo-webhooks")

    def test_webhook_signature_valid_supports_sha256_prefix(self):
        body = b'{"hello": "world"}'
        secret = "test-secret"
        digest = hmac.new(secret.encode("utf-8"), msg=body, digestmod=sha256).hexdigest()

        self.assertTrue(webhook_signature_valid(signature=f"sha256={digest}", body=body, app_secret=secret))
        self.assertFalse(webhook_signature_valid(signature="sha256=bad", body=body, app_secret=secret))

    def test_webhook_log_event_marks_duplicates(self):
        log, created = webhook_log_event(
            provider=WebhookProvider.META,
            external_event_id="evt-123",
            event_type="message",
            body=b'{"id":"evt-123"}',
            shop_id=str(self.shop.id),
        )

        self.assertTrue(created)
        self.assertEqual(log.status, WebhookProcessingStatus.PROCESSED)

        duplicate, duplicate_created = webhook_log_event(
            provider=WebhookProvider.META,
            external_event_id="evt-123",
            event_type="message",
            body=b'{"id":"evt-123"}',
            shop_id=str(self.shop.id),
            error_message="duplicate webhook",
        )

        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate.id, log.id)
        self.assertEqual(duplicate.status, WebhookProcessingStatus.DUPLICATE)
