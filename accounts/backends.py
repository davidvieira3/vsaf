import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from . import ldap
from .models import LdapRealm, UserProfile

log = logging.getLogger(__name__)


def split_login(raw, realm_choice):
    """Retourne (liste de realms à essayer, identifiant court). realm_choice : 'auto', 'local' ou id."""
    raw = raw.strip()
    realms = list(LdapRealm.objects.filter(enabled=True))
    if realm_choice not in ("", "auto", "local", None):
        realm = next((r for r in realms if str(r.pk) == str(realm_choice)), None)
        login = raw.split("\\", 1)[-1].split("@", 1)[0]
        return ([realm] if realm else []), login, False
    if realm_choice == "local":
        return [], raw, True
    if "\\" in raw:
        prefix, login = raw.split("\\", 1)
        return [r for r in realms if r.netbios and r.netbios.lower() == prefix.lower()], login, False
    if "@" in raw:
        login, suffix = raw.split("@", 1)
        matched = [r for r in realms if r.domain.lower() == suffix.lower()]
        if matched:
            return matched, login, False
        return [], raw, True  # e-mail utilisé comme login local
    return realms, raw, True  # auto : local d'abord, puis chaque domaine


class RealmAuthBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, realm=None, **kwargs):
        if not username or not password:
            return None
        realms, login, try_local = split_login(username, realm)
        if try_local:
            user = super().authenticate(request, username=login, password=password)
            if user is not None:
                return user
        for r in realms:
            try:
                result = ldap.authenticate(r, login, password)
            except ldap.LdapError as exc:
                log.warning("Realm %s : %s", r.name, exc)
                if request is not None:
                    request.ldap_error = f"{r.name} : {exc}"
                continue
            if result is None:
                continue
            if not result.role:
                if request is not None:
                    request.ldap_error = "Authentifié, mais aucun groupe AD n'ouvre l'accès à l'application."
                return None
            return self._sync_user(r, result)
        return None

    def _sync_user(self, realm, info):
        User = get_user_model()
        username = f"{info.login}@{realm.domain}".lower()
        user, created = User.objects.get_or_create(username=username)
        if created:
            user.set_unusable_password()
        elif not user.is_active:
            return None  # bloqué localement par un administrateur
        user.email = info.email
        user.first_name = (info.display_name or "")[:150]
        user.save()
        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                "realm": realm,
                "role": info.role,
                "display_name": info.display_name,
                "last_groups": "\n".join(info.groups),
            },
        )
        return user
