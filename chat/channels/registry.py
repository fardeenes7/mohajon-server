from chat.models import ChannelChoices
from chat.channels.base import BaseChannelAdapter
from chat.channels.facebook import FacebookAdapter
from chat.channels.whatsapp import WhatsAppAdapter
from chat.channels.web_widget import WebWidgetAdapter

ADAPTERS: dict[str, BaseChannelAdapter] = {
    ChannelChoices.FACEBOOK: FacebookAdapter(),
    ChannelChoices.WHATSAPP: WhatsAppAdapter(),
    ChannelChoices.WEB_WIDGET: WebWidgetAdapter(),
}

def get_adapter(channel: str) -> BaseChannelAdapter:
    if channel not in ADAPTERS:
        raise NotImplementedError(f"No adapter registered for channel: {channel}")
    return ADAPTERS[channel]
