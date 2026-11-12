from accounts.roles import user_role


def app_context(request):
    role = user_role(getattr(request, "user", None))
    return {
        "current_role": role,
        "is_admin": role == "admin",
        "is_operator": role in ("admin", "operator"),
    }
