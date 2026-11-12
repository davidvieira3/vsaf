"""Authentification Active Directory en LDAPS via ldap3 (pur Python)."""
import logging
import ssl
from dataclasses import dataclass, field

from ldap3 import FIRST, NONE, SUBTREE, Connection, Server, ServerPool, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn

from core.tls import ca_file

log = logging.getLogger(__name__)
IN_CHAIN = "1.2.840.113556.1.4.1941"


class LdapError(Exception):
    pass


@dataclass
class LdapUser:
    dn: str
    login: str
    display_name: str = ""
    email: str = ""
    groups: list = field(default_factory=list)
    role: str = ""


def _pool(realm):
    tls = None
    if realm.use_ssl:
        kwargs = {"validate": ssl.CERT_REQUIRED if realm.verify_cert else ssl.CERT_NONE}
        if realm.verify_cert:
            if realm.ca_pem.strip():
                kwargs["ca_certs_data"] = realm.ca_pem
            else:
                kwargs["ca_certs_file"] = ca_file()
        tls = Tls(**kwargs)
    servers = [
        Server(host, port=realm.port, use_ssl=realm.use_ssl, tls=tls, get_info=NONE, connect_timeout=realm.timeout)
        for host in realm.server_list()
    ]
    if not servers:
        raise LdapError("Aucun contrôleur de domaine configuré")
    return ServerPool(servers, FIRST, active=1, exhaust=True)


def _service_conn(realm):
    try:
        return Connection(
            _pool(realm),
            user=realm.bind_dn,
            password=realm.bind_password,
            auto_bind=True,
            receive_timeout=realm.timeout,
            raise_exceptions=True,
        )
    except LDAPException as exc:
        raise LdapError(f"Bind du compte de service impossible : {exc}") from exc


def _cn(dn):
    try:
        return parse_dn(dn)[0][1]
    except Exception:
        return dn


def _find_user(conn, realm, login):
    flt = realm.user_filter.replace("{username}", escape_filter_chars(login))
    conn.search(
        realm.user_base_dn,
        flt,
        SUBTREE,
        attributes=["displayName", "mail", "memberOf", "sAMAccountName", "userPrincipalName"],
    )
    entries = [e for e in conn.response if e.get("type") == "searchResEntry"]
    if len(entries) != 1:
        return None
    return entries[0]


def _groups(conn, realm, entry):
    dns = {g for g in entry["attributes"].get("memberOf", []) or []}
    if realm.nested_groups:
        base = realm.group_base_dn or realm.user_base_dn
        results = conn.extend.standard.paged_search(
            base,
            f"(member:{IN_CHAIN}:={escape_filter_chars(entry['dn'])})",
            SUBTREE,
            attributes=["cn"],
            paged_size=500,
            generator=False,
        )
        dns.update(r["dn"] for r in results if r.get("type") == "searchResEntry")
    return sorted(dns)


def _match(groups, wanted):
    dn_set = {g.lower() for g in groups}
    cn_set = {_cn(g).lower() for g in groups}
    for w in wanted:
        wl = w.lower()
        if ("=" in wl and wl in dn_set) or ("=" not in wl and wl in cn_set):
            return True
    return False


def role_from_groups(realm, groups):
    for role in ("admin", "operator", "viewer"):
        if _match(groups, realm.groups_for(role)):
            return role
    return realm.default_role or ""


def authenticate(realm, login, password):
    """Retourne un LdapUser si le mot de passe est bon, None sinon. Lève LdapError en cas de souci technique."""
    if not password:
        return None
    conn = _service_conn(realm)
    try:
        entry = _find_user(conn, realm, login)
        if entry is None:
            return None
        groups = _groups(conn, realm, entry)
    except LDAPException as exc:
        raise LdapError(f"Recherche LDAP en échec : {exc}") from exc
    finally:
        conn.unbind()

    user_conn = Connection(_pool(realm), user=entry["dn"], password=password, receive_timeout=realm.timeout)
    try:
        ok = user_conn.bind()
    except LDAPException as exc:
        raise LdapError(f"Bind utilisateur impossible : {exc}") from exc
    finally:
        try:
            user_conn.unbind()
        except Exception:
            pass
    if not ok:
        return None

    attrs = entry["attributes"]

    def one(name, default=""):
        v = attrs.get(name)
        if isinstance(v, list):
            v = v[0] if v else None
        return str(v) if v else default

    return LdapUser(
        dn=entry["dn"],
        login=one("sAMAccountName", login),
        display_name=one("displayName"),
        email=one("mail"),
        groups=groups,
        role=role_from_groups(realm, groups),
    )


def test_realm(realm, login=None):
    """Diagnostic pas-à-pas affiché dans l'interface d'administration."""
    steps = []
    try:
        conn = _service_conn(realm)
        steps.append(("ok", f"Connexion et bind du compte de service réussis ({realm.bind_dn})"))
    except LdapError as exc:
        return steps + [("error", str(exc))]
    try:
        if login:
            entry = _find_user(conn, realm, login)
            if entry is None:
                steps.append(("error", f"Utilisateur « {login} » introuvable (ou pas unique) avec le filtre configuré"))
            else:
                steps.append(("ok", f"Utilisateur trouvé : {entry['dn']}"))
                groups = _groups(conn, realm, entry)
                steps.append(("ok", f"{len(groups)} groupe(s) : " + ", ".join(_cn(g) for g in groups[:40])))
                role = role_from_groups(realm, groups)
                steps.append(("ok" if role else "warn", f"Rôle obtenu : {role or 'aucun → connexion refusée'}"))
    except LDAPException as exc:
        steps.append(("error", f"Erreur LDAP : {exc}"))
    finally:
        conn.unbind()
    return steps
