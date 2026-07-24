import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")
django.setup()

from catalog.models import Product
from django.db.models.signals import post_save

def debug_post_save(sender, instance, update_fields=None, **kwargs):
    print(f"DEBUG post_save: {update_fields}")

post_save.connect(debug_post_save, sender=Product)

p = Product.objects.first()
if p:
    p.save(update_fields=["embedding", "vector_status", "updated_at"])
else:
    print("No products found")
