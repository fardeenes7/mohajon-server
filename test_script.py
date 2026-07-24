import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mohajon.settings")
django.setup()

from catalog.models import Product
from django.db.models.signals import post_save

def debug_post_save(sender, instance, update_fields=None, **kwargs):
    print(f"DEBUG update_fields: {update_fields}")

post_save.connect(debug_post_save, sender=Product)

p = Product.objects.first()
if p:
    p.save(update_fields=["embedding", "vector_status", "updated_at"])
    print(f"DEBUG finished")
else:
    print("No products")
