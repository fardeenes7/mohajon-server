from django.urls import path
from chat.api.views import (
    AgentSendView,
    FAQDetailView,
    FAQListCreateView,
    HumanTakeoverView,
    InboxDetailView,
    InboxListView,
    ChannelWebhookView,
)
from chat.api.oauth_views import (
    WhatsAppOAuthStartView,
    WhatsAppOAuthCallbackView,
    WhatsAppOAuthSaveView,
    WhatsAppConfigView,
)


urlpatterns = [
    # Webhook (B-01)
    path("webhook/", ChannelWebhookView.as_view(channel="FACEBOOK"), name="messenger-webhook-legacy"),
    path("webhooks/<str:channel_id>/", ChannelWebhookView.as_view(), name="channel-webhook"),
    # Inbox (F-01)
    path("inbox/", InboxListView.as_view(), name="messenger-inbox-list"),
    path("inbox/<str:psid>/", InboxDetailView.as_view(), name="messenger-inbox-detail"),
    # Human takeover (F-02)
    path("takeover/", HumanTakeoverView.as_view(), name="messenger-takeover"),
    # Agent send (F-03)
    path("send/", AgentSendView.as_view(), name="messenger-agent-send"),
    # FAQ (G-01)
    path("faq/", FAQListCreateView.as_view(), name="messenger-faq-list"),
    path("faq/<str:pk>/", FAQDetailView.as_view(), name="messenger-faq-detail"),

    # WhatsApp Configuration & OAuth
    path("whatsapp/oauth/start/", WhatsAppOAuthStartView.as_view(), name="whatsapp-oauth-start"),
    path("whatsapp/oauth/callback/", WhatsAppOAuthCallbackView.as_view(), name="whatsapp-oauth-callback"),
    path("whatsapp/oauth/save/", WhatsAppOAuthSaveView.as_view(), name="whatsapp-oauth-save"),
    path("whatsapp/config/", WhatsAppConfigView.as_view(), name="whatsapp-config"),
]
