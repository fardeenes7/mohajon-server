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
    response = client.embeddings.create(
        model="google/gemini-embedding-2",
        input="test",
        encoding_format="float"
    )
    print("Success:", len(response.data[0].embedding))
except Exception as e:
    print(f"Exception: {e}")
