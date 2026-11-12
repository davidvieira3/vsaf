import re

import requests
import urllib3

from core.tls import requests_verify

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
MIB = 1024**2


def session(system):
    sess = requests.Session()
    sess.verify = requests_verify(system.verify_ssl)
    sess.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    return sess


def check(resp):
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text[:300]
        raise RuntimeError(f"HTTP {resp.status_code} sur {resp.request.method} {resp.url.split('?')[0]} : {detail}")
    return resp


def pct(used, total):
    return round(100.0 * used / total, 1) if total else None


def ratio(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if v > 0 else None


_HEX = re.compile(r"[^0-9a-f]")


def normalize_wwn(value):
    """naa.60002AC0... / eui.xxx / 60:00:2a:c0 → 60002ac0... (hexadécimal minuscule)."""
    v = (value or "").strip().lower()
    for prefix in ("naa.", "eui.", "0x"):
        if v.startswith(prefix):
            v = v[len(prefix):]
    return _HEX.sub("", v)
