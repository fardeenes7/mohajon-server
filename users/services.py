# users/services.py
from .models import User
from .tasks import send_verification_email_task
from django.db import transaction

def user_register(*, email: str, password: str) -> User:
    """
    Registers a new user and safely offloads the email handshake.
    """
    with transaction.atomic():
        user = User.objects.create_user(email=email, password=password)
        
    # Transaction committed, safe to queue celery task
    # Real link would abstract the frontend auth endpoint
    verification_url = f"https://app.mohajon.io/auth/verify?email={email}"
    send_verification_email_task.delay(user.email, verification_url)
    
    return user
