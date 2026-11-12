"""Gestion centralisée de la confiance TLS (CA internes collées dans les paramètres)."""
import hashlib
import os
import ssl
import tempfile

import certifi

from .models import AppSettings


def _custom_pem():
    try:
        return AppSettings.get().ca_bundle.strip()
    except Exception:  # base pas encore migrée
        return ""


def ca_file():
    """Chemin d'un bundle = CA publiques + CA internes, utilisable par requests (verify=...)."""
    pem = _custom_pem()
    if not pem:
        return certifi.where()
    digest = hashlib.sha256(pem.encode()).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), f"infra-inventory-ca-{digest}.pem")
    if not os.path.exists(path):
        with open(certifi.where(), encoding="utf-8") as src, open(path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
            dst.write("\n")
            dst.write(pem)
            dst.write("\n")
    return path


def requests_verify(verify: bool):
    return ca_file() if verify else False


def ssl_context(verify: bool) -> ssl.SSLContext:
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=ca_file())
