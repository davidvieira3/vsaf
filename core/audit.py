from .models import AuditLog


def client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")) or None


def audit(request, action, target="", detail=""):
    user = getattr(request, "user", None)
    username = user.get_username() if user is not None and user.is_authenticated else ""
    AuditLog.objects.create(
        username=username, action=action, target=str(target)[:255], detail=detail, ip=client_ip(request)
    )
