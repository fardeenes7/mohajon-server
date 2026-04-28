from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

@shared_task(queue="high_priority")
def send_verification_email_task(user_email: str, verification_link: str):
    """
    Sends an async welcome/verification email via Celery.
    Routed to 'high_priority' queue to ensure user can log in immediately.
    """
    subject = "Welcome to Mohajon - Please verify your email"
    message = f"""
    Hello,

    Welcome to Mohajon! Verify your account by clicking the link below:
    {verification_link}

    If you did not request this, please ignore this email.

    Regards,
    Team Mohajon
    """
    
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user_email],
            fail_silently=False,
        )
        logger.info(f"Successfully sent verification email to {user_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {user_email}: {str(e)}")
        # Note: Celery retry logic could be added here
        return False
