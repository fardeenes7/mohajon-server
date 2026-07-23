from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class WaitlistRedisThrottle(AnonRateThrottle):
    """
    Limits the waitlist POST endpoint to prevent spam.
    Configured dynamically. Fallback is 3 requests per hour per IP.
    Requires Redis cache backend to be functioning.
    """
    rate = "3/hour"

    def allow_request(self, request, view):
        if getattr(settings, "DEBUG", False):
            return True
        return super().allow_request(request, view)


class SocialOAuthThrottle(UserRateThrottle):
    scope = "social_oauth"

    def allow_request(self, request, view):
        if getattr(settings, "DEBUG", False):
            return True
        return super().allow_request(request, view)


class SocialPublishThrottle(UserRateThrottle):
    scope = "social_publish"

    def allow_request(self, request, view):
        if getattr(settings, "DEBUG", False):
            return True
        return super().allow_request(request, view)
