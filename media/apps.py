from django.apps import AppConfig


class MediaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "media"
    label = "mohajon_media"  # avoid collision with Django's built-in media handling
