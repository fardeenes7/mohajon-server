"""
OpenAI tool implementations for the Messenger chatbot (EPIC C-03).

Each function maps directly to an OpenAI function definition and is called
by ai_engine.py after the model requests a tool invocation.

Constraint: confirm_order and get_invoice_payment_link may only be called
if a valid order_draft exists in ConversationBotState.
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from chat.services.bot_state import bot_state_get_order_draft


# ---------------------------------------------------------------------------
# Tool JSON schemas (passed to OpenAI as `tools`)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search the shop's product catalog. Returns top matching products with name, price, image, stock status and storefront link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": "Get full product info including variants, stock per variant, and description.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_details",
            "description": "Look up an order by order ID or by PSID (returns last order for this customer). Returns status, courier tracking link, and estimated delivery.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_identifier": {"type": "string", "description": "Order UUID or 'last' to fetch last order for this customer"},
                },
                "required": ["order_identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_order_draft",
            "description": "Create a draft order object and store it in ConversationBotState. Returns a summary for the AI to present before confirming. Does NOT write to the DB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                    "variant_id": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1},
                },
                "required": ["product_id", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_available_payment_methods",
            "description": "Returns the shop's active payment methods (COD, bKash, etc.) and whether the advance delivery fee is enabled.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_invoice_payment_link",
            "description": "Generates a PaymentInvoice token and returns the full /pay/{token} URL for non-COD orders. Requires a confirmed order_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_order",
            "description": "Finalise the order_draft from ConversationBotState, writes to DB, triggers order FSM. MUST call prepare_order_draft first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "shipping_address": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "phone": {"type": "string"},
                            "address_line": {"type": "string"},
                            "division": {"type": "string"},
                            "district": {"type": "string"},
                            "thana": {"type": "string"},
                        },
                        "required": ["name", "phone", "address_line"],
                    },
                    "payment_method": {"type": "string", "enum": ["COD", "BKASH", "SSLCOMMERZ"]},
                },
                "required": ["shipping_address", "payment_method"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_faq",
            "description": "Semantic search over the shop's FAQ and policy entries using pgvector. Returns top 3 Q&A pairs above 0.75 similarity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_older_messages",
            "description": "Fetch messages older than a timestamp from Postgres when context window is insufficient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "before_timestamp": {"type": "integer"},
                    "limit": {"type": "integer", "default": 20, "maximum": 50},
                },
                "required": ["before_timestamp"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_saved_addresses",
            "description": (
                "Return this customer's previously-used successful delivery "
                "addresses (from orders that were actually delivered), resolved "
                "across all their channels. Call this before asking a returning "
                "customer for their shipping address so you can offer to reuse a "
                "known one. Returns an empty list for first-time customers — in "
                "that case just ask for the address normally."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------

def execute_tool(
    *,
    tool_name: str,
    tool_args: dict,
    shop_id: str,
    psid: str,
    page_id: str,
    channel: str = "FACEBOOK",
) -> dict:
    """
    Dispatch a tool call from the AI engine.
    Returns a result dict that is fed back into the OpenAI tool loop.
    Errors are returned as {"error": "..."} so the AI can handle gracefully.
    """
    try:
        if tool_name == "search_products":
            return _search_products(shop_id=shop_id, **tool_args)
        elif tool_name == "get_product_details":
            return _get_product_details(shop_id=shop_id, **tool_args)
        elif tool_name == "get_order_details":
            return _get_order_details(shop_id=shop_id, psid=psid, channel=channel, **tool_args)
        elif tool_name == "prepare_order_draft":
            return _prepare_order_draft(shop_id=shop_id, page_id=page_id, psid=psid, **tool_args)
        elif tool_name == "get_available_payment_methods":
            return _get_available_payment_methods(shop_id=shop_id)
        elif tool_name == "get_invoice_payment_link":
            return _get_invoice_payment_link(shop_id=shop_id, page_id=page_id, psid=psid, **tool_args)
        elif tool_name == "confirm_order":
            return _confirm_order(shop_id=shop_id, page_id=page_id, psid=psid, channel=channel, **tool_args)
        elif tool_name == "search_faq":
            return _search_faq(shop_id=shop_id, **tool_args)
        elif tool_name == "get_older_messages":
            return _get_older_messages(shop_id=shop_id, psid=psid, **tool_args)
        elif tool_name == "get_saved_addresses":
            return _get_saved_addresses(shop_id=shop_id, channel=channel, channel_identity=psid)
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Individual tool implementations
# ---------------------------------------------------------------------------

def _search_products(*, shop_id: str, query: str, limit: int = 5) -> dict:
    from catalog.models import Product
    from django.contrib.postgres.search import SearchQuery, SearchRank

    fts_query = SearchQuery(query, config="english")
    qs = (
        Product.objects.filter(shop_id=shop_id, status="PUBLISHED", deleted_at__isnull=True)
        .annotate(rank=SearchRank("search_vector", fts_query))
        .filter(search_vector=fts_query)
        .order_by("-rank")[:limit]
    )

    results = []
    for p in qs:
        results.append({
            "id": str(p.id),
            "name": p.name,
            "base_price": str(p.base_price),
            "description": p.description[:100] + "..." if p.description else "",
            "status": p.status,
            "total_stock": p.total_stock,
        })
    return {"products": results}


def _get_product_details(*, shop_id: str, product_id: str) -> dict:
    from catalog.models import Product, ProductVariant

    try:
        product = Product.objects.prefetch_related("variants").get(
            id=product_id, shop_id=shop_id, deleted_at__isnull=True
        )
    except Product.DoesNotExist:
        return {"error": "Product not found"}

    variants = [
        {
            "id": str(v.id),
            "attributes": v.attributes,
            "price_override": str(v.price_override) if v.price_override else None,
            "stock_quantity": v.stock_quantity,
        }
        for v in product.variants.filter(deleted_at__isnull=True)
    ]
    return {
        "id": str(product.id),
        "name": product.name,
        "base_price": str(product.base_price),
        "description": product.description,
        "status": product.status,
        "total_stock": product.total_stock,
        "variants": variants,
    }


def _get_order_details(*, shop_id: str, psid: str, order_identifier: str, channel: str = "FACEBOOK") -> dict:
    from orders.selectors import order_list_for_customer
    from identity.services.preload import _person_for_channel

    # SECURITY: every lookup is scoped to this channel actor (actor_reference ==
    # the PSID/waid captured at checkout), NOT just shop_id. Scoping by shop
    # alone leaks any customer's order to whoever is chatting. Without a PSID we
    # cannot safely attribute an order to this customer, so we refuse.
    if not psid:
        return {"error": "Unable to identify this customer."}

    person = _person_for_channel(shop_id=shop_id, channel=channel, channel_identity=psid)
    if not person:
        return {"error": "Unable to identify this customer."}

    base_qs = order_list_for_customer(customer_profile_id=str(person.id), shop_id=shop_id)

    try:
        if order_identifier == "last":
            order = base_qs.first()
            if not order:
                return {"error": "No orders found for this customer."}
        else:
            order = base_qs.get(id=order_identifier)
    except Exception:
        return {"error": "Order not found."}

    return {
        "id": str(order.id),
        "status": order.status,
        "total_amount": str(order.total_amount),
        "currency": order.currency,
        "created_at": order.created_at.isoformat(),
    }


def _prepare_order_draft(
    *,
    shop_id: str,
    page_id: str,
    psid: str,
    product_id: str,
    quantity: int,
    variant_id: str | None = None,
) -> dict:
    from catalog.models import Product, ProductVariant
    from chat.services.bot_state import bot_state_set_order_draft

    try:
        product = Product.objects.get(id=product_id, shop_id=shop_id, deleted_at__isnull=True)
    except Product.DoesNotExist:
        return {"error": "Product not found."}

    variant = None
    if variant_id:
        try:
            variant = ProductVariant.objects.get(id=variant_id, shop_id=shop_id, deleted_at__isnull=True)
        except ProductVariant.DoesNotExist:
            return {"error": "Variant not found."}

    unit_price = Decimal(str(variant.price_override if variant and variant.price_override else product.base_price))
    line_total = unit_price * quantity

    draft = {
        "product_id": str(product.id),
        "product_name": product.name,
        "variant_id": str(variant.id) if variant else None,
        "variant_attributes": variant.attributes if variant else {},
        "quantity": quantity,
        "unit_price": str(unit_price),
        "line_total": str(line_total),
        "currency": "BDT",
    }
    bot_state_set_order_draft(page_id=page_id, psid=psid, draft=draft)
    return {"draft": draft, "summary": f"{quantity}× {product.name} — {line_total} BDT"}


def _get_available_payment_methods(*, shop_id: str) -> dict:
    from shops.selectors import get_shop_settings
    settings_obj = get_shop_settings(shop_id)
    if settings_obj:
        advance_fee = settings_obj.mandatory_advance_fee_bdt
    else:
        advance_fee = 0

    return {
        "methods": ["COD"],       # v0.6 scope: online gateways enabled in v0.8
        "advance_delivery_fee_bdt": advance_fee,
        "note": "Online payment (bKash / SSLCommerz) will be available soon.",
    }


def _get_invoice_payment_link(*, shop_id: str, page_id: str, psid: str, order_id: str) -> dict:
    draft = bot_state_get_order_draft(page_id=page_id, psid=psid)
    if not draft:
        return {"error": "No active order draft.  Call prepare_order_draft first."}

    from orders.selectors import order_get_by_id
    from orders.services.invoices import payment_invoice_create
    from shops.selectors import get_shop

    try:
        order = order_get_by_id(order_id=order_id, shop_id=shop_id)
        shop = get_shop(shop_id)
        if not shop:
            return {"error": "Order or shop not found."}
    except Exception:
        return {"error": "Order or shop not found."}

    invoice = payment_invoice_create(order=order)
    base = f"https://{shop.subdomain}.mohajon.store"
    return {"payment_url": f"{base}/pay/{invoice.token}", "expires_in_minutes": 30}


def _confirm_order(
    *,
    shop_id: str,
    page_id: str,
    psid: str,
    shipping_address: dict,
    payment_method: str,
    channel: str = "FACEBOOK",
) -> dict:
    draft = bot_state_get_order_draft(page_id=page_id, psid=psid)
    if not draft:
        return {"error": "No active order draft.  Call prepare_order_draft first."}

    from orders.services.checkout import checkout_create_order
    from orders.services.transitions import order_transition
    from chat.services.bot_state import bot_state_clear_order_draft
    from fraud.services.risk import check_customer_risk
    from fraud.selectors import get_fraud_config
    from shops.selectors import get_shop

    items = [{
        "product_id": draft["product_id"],
        "variant_id": draft.get("variant_id"),
        "quantity": draft["quantity"],
    }]

    # Check fraud risk
    shop = get_shop(shop_id)
    if not shop:
        return {"error": "Shop not found."}
        
    risk = check_customer_risk(shop, shipping_address.get("phone"), actor_reference=psid)
    fraud_config = get_fraud_config(shop_id)
    
    if risk["is_high_risk"] and fraud_config.block_high_risk:
        return {"error": "This account or phone number is flagged for high risk. COD is currently disabled."}

    try:
        order = checkout_create_order(
            shop_id=shop_id,
            items=items,
            payment_method="COD" if payment_method == "COD" else "PREPAID",
            actor_reference=psid,
            # Identity graph: the chat path is the only place these signals are
            # captured (they were previously discarded after the risk check).
            # channel_identity is the channel-scoped endpoint id (Messenger PSID
            # or WhatsApp waid) — a distinct value space per channel. `channel`
            # is threaded from the inbound turn so a WhatsApp waid is never
            # stored as a FACEBOOK psid (was hardcoded to FACEBOOK here).
            shipping_address=shipping_address,
            channel=channel,
            channel_identity=psid,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    if payment_method == "COD":
        order_transition(
            order=order,
            to_status="CONFIRMED",
            actor_user_id=None,
            actor_role="MANAGER",
            reason="Chatbot COD confirmation",
        )

    bot_state_clear_order_draft(page_id=page_id, psid=psid)

    return {
        "order_id": str(order.id),
        "status": order.status,
        "total_amount": str(order.total_amount),
        "payment_method": payment_method,
    }


def _search_faq(*, shop_id: str, query: str) -> dict:
    from core.services.ai_gateway import AIGateway
    from chat.models import FAQEntry
    from catalog.models import Product
    from pgvector.django import CosineDistance

    gateway = AIGateway(shop_id)
    query_vector = gateway.call_embedding(text=query)

    # 1. Search Policies & FAQ
    faq_results = (
        FAQEntry.objects
        .filter(shop_id=shop_id, is_active=True, deleted_at__isnull=True, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", query_vector))
        .filter(distance__lte=0.25)   # cosine distance <= 0.25 ≈ similarity >= 0.75
        .order_by("distance")[:3]
    )

    # 2. Search Product Specs (RAG)
    product_results = (
        Product.objects
        .filter(shop_id=shop_id, status="PUBLISHED", deleted_at__isnull=True, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", query_vector))
        .filter(distance__lte=0.25)
        .order_by("distance")[:3]
    )

    combined_results = []
    
    for r in faq_results:
        combined_results.append({
            "type": "policy_faq",
            "category": r.category,
            "question": r.question,
            "answer": r.answer,
            "distance": float(r.distance),
        })
        
    for r in product_results:
        # Build a concise summary of the specs
        specs_summary = ", ".join([f"{k}: {v}" for k, v in r.specifications.items()])
        combined_results.append({
            "type": "product_specs",
            "product_name": r.name,
            "description": r.description[:200] + "...",
            "specifications": specs_summary,
            "distance": float(r.distance),
        })

    # Sort by distance and take top 3
    combined_results.sort(key=lambda x: x["distance"])
    final_results = combined_results[:3]

    if not final_results:
        return {"results": [], "message": "No matching knowledge found."}

    return {"results": final_results}


def _get_older_messages(*, shop_id: str, psid: str, before_timestamp: int, limit: int = 20) -> dict:
    from chat.selectors import message_list_for_psid

    messages = message_list_for_psid(
        shop_id=shop_id,
        psid=psid,
        limit=min(limit, 50),
        before_timestamp=before_timestamp,
    )
    return {"messages": messages}


def _get_saved_addresses(*, shop_id: str, channel: str, channel_identity: str) -> dict:
    from identity.services.preload import preload_addresses_for_channel

    addresses = preload_addresses_for_channel(
        shop_id=shop_id, channel=channel, channel_identity=channel_identity
    )
    if not addresses:
        return {
            "addresses": [], 
            "message": "No saved addresses found for this customer. Please ask them to provide their address."
        }
    return {"addresses": addresses}
