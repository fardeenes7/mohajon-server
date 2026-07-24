import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv('/home/fardeen/Projects/mahajon/server/.env')

client = OpenAI(
    api_key=os.environ.get("EMBEDDING_PROVIDER_API_KEY"),
    base_url=os.environ.get("EMBEDDING_PROVIDER_URL")
)

try:
    raw_response = client.embeddings.with_raw_response.create(
        model="google/gemini-embedding-2",
        input="test"
    )
    print(raw_response.content)
except Exception as e:
    print(f"Exception: {e}")
