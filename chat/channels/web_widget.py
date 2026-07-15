from typing import Any
from django.http import HttpRequest, HttpResponse

from chat.channels.base import BaseChannelAdapter

class WebWidgetAdapter(BaseChannelAdapter):
    
    def handle_webhook_handshake(self, request: HttpRequest) -> HttpResponse:
        # Web widget likely doesn't use a challenge handshake since it's our own client
        return HttpResponse("OK")

    def verify_webhook_signature(self, request: HttpRequest) -> bool:
        # TODO: Implement JWT or HMAC validation for our Web Widget API
        raise NotImplementedError("WebWidgetAdapter not yet implemented")

    def parse_webhook_payload(self, request: HttpRequest) -> list[dict[str, Any]]:
        # TODO: Parse Web Widget payload into standard event dicts
        raise NotImplementedError("WebWidgetAdapter not yet implemented")

    def send_text(self, shop_id: str, channel_identity: str, text: str, **kwargs: Any) -> None:
        # TODO: Send SSE or WebSocket event to the connected Web Widget client
        raise NotImplementedError("WebWidgetAdapter not yet implemented")
