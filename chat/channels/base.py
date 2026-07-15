import abc
from typing import Any
from django.http import HttpRequest, HttpResponse

class BaseChannelAdapter(abc.ABC):
    @abc.abstractmethod
    def handle_webhook_handshake(self, request: HttpRequest) -> HttpResponse:
        """
        Handle verification challenges (e.g., GET hub.challenge for Meta).
        If the channel doesn't require a handshake, it can return a generic 200 OK.
        """
        pass
        
    @abc.abstractmethod
    def verify_webhook_signature(self, request: HttpRequest) -> bool:
        """Validate the cryptographic signature of an incoming payload."""
        pass
        
    @abc.abstractmethod
    def parse_webhook_payload(self, request: HttpRequest) -> list[dict[str, Any]]:
        """
        Parse the vendor-specific payload into a normalized list of events.
        Each event dict will contain the normalized data needed to trigger
        `process_inbound_message.delay(...)`.
        Expected dict keys:
        - shop_id: str
        - page_id: str (channel-specific tenant identifier)
        - psid: str (user identifier)
        - message_text: str | None
        - mid: str
        - timestamp: int
        - messaging_type: str ("message", "postback", "comment", etc.)
        - postback_payload: str | None
        - comment_data: dict | None
        - page_access_token: str | None
        """
        pass
        
    @abc.abstractmethod
    def send_text(self, shop_id: str, channel_identity: str, text: str, **kwargs: Any) -> None:
        """
        Send an outbound text message to a user.
        Responsible for looking up its own credentials internally.
        """
        pass
