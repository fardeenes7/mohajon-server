import os
import requests
from dotenv import load_dotenv

load_dotenv('/home/fardeen/Projects/mahajon/server/.env')

url = "https://openrouter.ai/api/v1/embeddings"
headers = {
    "Authorization": f"Bearer {os.environ.get('EMBEDDING_PROVIDER_API_KEY')}",
    "Content-Type": "application/json"
}
data = {
    "model": "google/gemini-embedding-2",
    "input": "test"
}

response = requests.post(url, headers=headers, json=data)
print(f"Status Code: {response.status_code}")
print(f"Response Body: {response.text}")
