import os
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
        input="test"
    )
    print(response)
except Exception as e:
    print(f"Error: {e}")
