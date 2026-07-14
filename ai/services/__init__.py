from ai.services.ai_gateway import AIGateway
from ai.models import AIModelUsage

def generate_description(shop_id: str, prompt: str, **kwargs) -> str:
    """Generate product description using chat completion."""
    gateway = AIGateway(shop_id=shop_id)
    return gateway.call_chat_completion(
        messages=[
            {"role": "system", "content": "You are a world-class e-commerce copywriter. Always return content formatted in clean, minimal HTML."},
            {"role": "user", "content": prompt}
        ],
        **kwargs
    )

def generate_image(shop_id: str, prompt: str, **kwargs) -> str:
    """Generate image and return URL."""
    gateway = AIGateway(shop_id=shop_id)
    return gateway.call_image_generation(prompt=prompt, **kwargs)

def generate_embedding(shop_id: str, text: str, **kwargs) -> list[float]:
    """Generate embeddings for semantic search."""
    gateway = AIGateway(shop_id=shop_id)
    return gateway.call_embedding(text=text, **kwargs)

def chat_engine(shop_id: str, reference_id: str, messages: list, tools: list, **kwargs) -> dict:
    """
    Stateful chat completion for the tool loop.
    Returns the response, usage data, etc. The caller handles the loop and then calls log_accumulated_usage.
    """
    gateway = AIGateway(shop_id=shop_id, reference_id=reference_id)
    return gateway.call_chat_with_tools(messages=messages, tools=tools, **kwargs)
