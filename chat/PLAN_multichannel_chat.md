# Plan: Full-functional multi-channel live chat (Messenger + WhatsApp) with human oversight + near-real-time

All paths under `server/`.

## Goal
Both channels fully send+receive; one channel-generic agent inbox; real unread state; human oversight with a hardcoded BOT/HUMAN global toggle; near-real-time delivery to agents at scale via Django Channels (WebSocket) over the existing Redis + ASGI stack.

---

## Section 1 — Bug fixes (do first; smallest blast radius)

1.1 **`messenger` queue is never consumed** → inbound chat is dead. Add `,messenger` to the general worker `-Q` list in `docker-compose.prod.yml` (~L167) and `docker-compose.yml`. Queue already declared in `mohajon/celery.py`; no code change.

1.2 **WhatsApp outbound misrouted** — `chat/tasks/__init__.py` always sends via the Facebook Send API. Replace `send_api.send_text(...)` calls (greeting reply, AI reply TODO ~L135, and each branch of `_handle_postback`) with `get_adapter(channel).send_text(shop_id=, channel_identity=psid, text=, page_id=page_id)`. Thread `channel` into `_handle_postback`. Adapters resolve their own credentials.

1.3 **WhatsApp token truncation** — `chat/models.py:154` `WhatsAppConfig.access_token = CharField(max_length=255)` → `TextField()` (mirrors marketing.SocialConnection). Migration in 2.3.

1.4 **History collision** — `chat/selectors.py::message_list_for_psid` filters `channel_identity` only. Add required `channel` param; filter `conversation__channel=channel, conversation__channel_identity=psid`. Update callers in `engine.run_ai_turn` and `InboxDetailView`.

---

## Section 2 — Models + migration

2.1 **Conversation unread** (`chat/models.py`): add `has_unread=BooleanField(default=False, db_index=True)`, `unread_count=PositiveIntegerField(default=0)`, `last_read_at=DateTimeField(null=True, blank=True)`.
- Bump on INBOUND persist only: `filter(pk=...).update(has_unread=True, unread_count=F('unread_count')+1)`.
- Reset on agent open (`InboxDetailView`): `has_unread=False, unread_count=0, last_read_at=now()` via a `mark_conversation_read()` selector.

2.2 **WhatsAppConfig token** → TextField (from 1.3).

2.3 One migration `chat/migrations/00XX_...`: 3× AddField on Conversation + AlterField on WhatsAppConfig.access_token. No backfill.

---

## Section 3 — Channel-generic inbox/send

3.1 API shape: frontend sends `channel` (ChannelChoices), defaults to FACEBOOK when absent (back-compat).
3.2 Serializers (`chat/api/serializers.py`): `AgentMessageSerializer` add `channel` (default FACEBOOK), `page_id` optional. `ConversationListSerializer`: drop metadata-based `has_unread`; expose real `has_unread`/`unread_count`/`last_read_at`; keep `page_id` from metadata.
3.3 Selectors: `unread_inbox_count_for_shop` uses real field (remove try/except); `message_list_for_psid` channel-aware; add `mark_conversation_read`.
3.4 Views: `InboxDetailView` reads `?channel=`, passes through, marks read. `AgentSendView` already routes via `get_adapter(channel)` — add realtime emit after persist, no unread bump.

---

## Section 4 — Bot/Human global toggle
Create `chat/constants.py`:
```
RESPONSE_MODE = "BOT"   # "BOT" = AI auto-replies; "HUMAN" = human-only, AI not invoked
def bot_autoresponds() -> bool: return RESPONSE_MODE == "BOT"
```
In `process_inbound_message` (after human_active check, in the message branch, after `if not message_text`): if `not bot_autoresponds()` → persist inbound (via `engine._persist_message`, which bumps unread + emits realtime) and return without greeting/AI. Also gate `_handle_postback` and comment-autoreply behind the toggle. Global override layered on top of per-conversation `bot_state` takeover; flip requires restart.

---

## Section 5 — Real-time (Django Channels + Redis channel layer)
5.1 Add `channels`, `channels-redis` to requirements. (UvicornWorker already serves ASGI; no daphne.)
5.2 settings: add `'channels'`, `ASGI_APPLICATION`, `CHANNEL_LAYERS` (RedisChannelLayer reusing `REDIS_URL`).
5.3 `mohajon/asgi.py`: `ProtocolTypeRouter` — http = existing; websocket = SimpleJWT WS auth middleware → `chat/routing.py` URLRouter.
5.4 `chat/consumers.py` `AgentInboxConsumer`: auth-required; validate user is member of `?tenant=<shop_id>`; join per-tenant group `chat_shop_<shop_id>`; handlers `chat_message`, `conversation_update`.
5.5 `chat/services/realtime.py`: sync `publish_chat_event(shop_id, event_type, payload)` using `get_channel_layer()`+`async_to_sync(group_send)` — works from Celery worker (writes to Redis) and DRF. Wrappers `publish_new_message`, `publish_conversation_update`.
5.6 Emit at the two persist chokepoints: `engine._persist_message` and `AgentSendView`. Prefer one shared `persist_and_publish` helper.
5.7 Docker: web unchanged (ASGI). Ops: proxy must forward WS Upgrade headers on `ws/`.

---

## Section 6 — Reuse
`registry.get_adapter`, `bot_state_*`, `marketing.get_connection_by_page_id`, `ai_gateway.AIGateway`, `TenantModel/SoftDeleteModel`, `identity.get_or_create_channel_actor`, `engine._persist_message`.

## Section 7 — Verification
Extend `chat/tests.py`: channel routing (mock requests.post → correct endpoint per channel), toggle (patch RESPONSE_MODE, assert AIGateway not called in HUMAN), unread counting, channel collision, serializer real fields, >255-char token save, queue name. Realtime: `WebsocketCommunicator` + `InMemoryChannelLayer`, assert receipt + auth/tenant rejection. Manual: worker consumes messenger; real WA message replies via WA; two authed tabs get live events; toggle smoke.

## Section 8 — Out of scope
FB posting; WebWidgetAdapter (stays stubbed); Meta webhook subscription + `pages_messaging` scope (pre-existing onboarding gap — flag as follow-up, may block live inbound testing); runtime toggling; media/attachment send.

## Execution order
1) compose `-Q messenger` → 2) tasks routing → 3) model fields+token+migration → 4) selectors → 5) serializers → 6) views → 7) constants+toggle → 8) realtime stack → 9) tests+smoke.
