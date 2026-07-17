import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status

from shipping.services import apply_status_from_webhook
from webhooks.models import WebhookLog, WebhookProvider, WebhookProcessingStatus
from webhooks.services import webhook_signature_valid

logger = logging.getLogger(__name__)


class CourierWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, provider_code=None, *args, **kwargs):
        provider_code = provider_code or request.data.get("provider")

        # Signature check similar to chat FacebookAdapter
        signature = request.META.get("HTTP_X_HUB_SIGNATURE_256", "")
        if not webhook_signature_valid(signature=signature, body=request.body):
            logger.warning("Courier webhook: invalid signature.")
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        
        payload = request.data
        external_id = str(payload.get("consignment_id") or payload.get("id") or "")
        
        # Deduplication / Logging
        webhook_log = WebhookLog.objects.create(
            provider=WebhookProvider.COURIER,
            external_event_id=external_id or "unknown",
            status=WebhookProcessingStatus.PROCESSED,
        )

        try:
            apply_status_from_webhook(payload=payload, provider=provider_code)
        except Exception as e:
            logger.exception("Courier webhook failed")
            webhook_log.status = WebhookProcessingStatus.FAILED
            webhook_log.error_message = str(e)
            webhook_log.save(update_fields=["status", "error_message"])
            return Response({"status": "error", "message": str(e)}, status=400)

        return Response({"status": "success"}, status=200)
