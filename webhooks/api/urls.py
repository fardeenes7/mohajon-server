# The Meta webhook ingest endpoint that previously lived here was a no-op: it
# verified the signature, wrote a WebhookLog row, and returned "accepted"
# without ever processing the payload. The live Meta path is
# chat.api.views.ChannelWebhookView (routed under /api/v1/chat/), which parses
# and dispatches inbound messages. This app is now pure infrastructure —
# webhooks.services (signature validation + idempotent WebhookLog) is retained
# and is intended to back the chat webhook path in a later phase.
urlpatterns = []
