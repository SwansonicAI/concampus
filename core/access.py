from functools import wraps

from django.core.exceptions import PermissionDenied


def require(*roles):
    """View decorator: the user needs one of these roles (office names, 'student', 'applicant', 'teacher')."""

    def decorator(view):
        @wraps(view)
        def guarded(request, *args, **kwargs):
            if not request.user.roles & set(roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return guarded

    return decorator
