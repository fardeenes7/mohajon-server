import django.dispatch

# Emitted after a Meta Page SocialConnection is created or refreshed.
# Receivers (e.g. chat) use this to subscribe the page to Messenger webhooks
# and configure the Messenger profile. Kept in `marketing` so `marketing` never
# needs to import `chat` — the dependency stays one-way (chat -> marketing).
#
# Providing args: shop_id: str, page_id: str, connection_id: str
page_connected = django.dispatch.Signal()
