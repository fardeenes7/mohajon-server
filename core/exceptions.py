import math
from rest_framework.views import exception_handler
from rest_framework.exceptions import Throttled

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if isinstance(exc, Throttled) and response is not None:
        wait = exc.wait
        if wait is not None:
            seconds = int(wait)
            if seconds >= 3600:
                hours = math.ceil(seconds / 3600)
                time_str = f"{hours} hour{'s' if hours > 1 else ''}"
            elif seconds >= 60:
                minutes = math.ceil(seconds / 60)
                time_str = f"{minutes} minute{'s' if minutes > 1 else ''}"
            else:
                time_str = f"{seconds} second{'s' if seconds > 1 else ''}"
            
            response.data["detail"] = f"Too many requests. Please wait {time_str} before trying again."
        else:
            response.data["detail"] = "Too many requests. Please try again shortly."

    return response
