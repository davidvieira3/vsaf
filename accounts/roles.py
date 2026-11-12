from functools import wraps

from django.core.exceptions import PermissionDenied

ROLE_LEVEL = {"viewer": 1, "operator": 2, "admin": 3}


def user_role(user):
    if user is None or not user.is_authenticated:
        return None
    if user.is_superuser:
        return "admin"
    profile = getattr(user, "profile", None)
    try:
        return profile.role if profile else "viewer"
    except Exception:
        return "viewer"


def has_role(user, role):
    return ROLE_LEVEL.get(user_role(user) or "", 0) >= ROLE_LEVEL[role]


def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not has_role(request.user, role):
                raise PermissionDenied("Droits insuffisants")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
