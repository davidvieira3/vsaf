"""Chiffrement symétrique (Fernet) des secrets stockés en base."""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_fernet = None


def _get():
    global _fernet
    if _fernet is None:
        key = settings.APP_ENCRYPTION_KEY
        if not key:
            raise ImproperlyConfigured("APP_ENCRYPTION_KEY est vide : générez-la (voir README).")
        _fernet = Fernet(key.encode())
    return _fernet


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _get().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _get().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Secret illisible : APP_ENCRYPTION_KEY a changé depuis l'enregistrement.") from exc
