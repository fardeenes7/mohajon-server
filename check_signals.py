import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mohajon.settings")
django.setup()

from catalog.models import Product
from django.db.models.signals import post_save

receivers = post_save._live_receivers(Product)
for receiver in receivers:
    print(receiver.__module__, receiver.__name__)

