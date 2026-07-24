import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mohajon.settings")
django.setup()

from catalog.models import Product
from chat.signals import _VECTOR_ONLY_FIELDS

p = Product.objects.first()
if p:
    print("Found product")
    # Simulate update_product_embedding
    p.vector_status = "PENDING"
    p.save(update_fields=["embedding", "vector_status", "updated_at"])
    print("Saved product")
