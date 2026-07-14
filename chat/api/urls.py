from django.urls import path
from chat.api.views import (
    AgentSendView,
    FAQDetailView,
    FAQListCreateView,
    HumanTakeoverView,
    InboxDetailView,
    InboxListView,
    MetaWebhookView,
)

urlpatterns = [
    # Webhook (B-01)
    path("webhook/", MetaWebhookView.as_view(), name="messenger-webhook"),
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
]
