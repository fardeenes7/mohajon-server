import os
import django
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

try:
    django.setup()
except Exception:
    pass

import sys
print(sys.path)

