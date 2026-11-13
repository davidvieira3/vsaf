"""Configuration Django. Tout ce qui est propre à l'environnement vient des variables d'env (.env)."""
import os
from pathlib import Path

from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent


def env(key, default=None):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    return env(key, "1" if default else "0").lower() in ("1", "true", "yes", "on")


SECRET_KEY = env("DJANGO_SECRET_KEY", "change-me-insecure")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()] + ["127.0.0.1", "localhost"]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# Clé Fernet servant à chiffrer les mots de passe des vCenter / Proxmox / LDAP / BMC en base.
APP_ENCRYPTION_KEY = env("APP_ENCRYPTION_KEY", "")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "accounts",
    "inventory",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.app_context",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "inventory"),
        "USER": env("POSTGRES_USER", "inventory"),
        "PASSWORD": env("POSTGRES_PASSWORD", ""),
        "HOST": env("POSTGRES_HOST", "db"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = env("REDIS_URL", "redis://redis:6379/0")
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}

AUTHENTICATION_BACKENDS = ["accounts.backends.RealmAuthBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "inventory:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

SESSION_COOKIE_AGE = int(env("SESSION_HOURS", "8")) * 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000  # tableau d'annotation : sources × champs personnalisés

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = env("TZ", "Europe/Paris")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Celery -----------------------------------------------------------------
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = None
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TRACK_STARTED = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "dispatch-due-collections": {"task": "inventory.tasks.dispatch_due", "schedule": 60.0},
    "sync-hardware": {"task": "inventory.tasks.sync_all_hardware", "schedule": crontab(minute=15, hour="*/6")},
    "purge-runs": {"task": "inventory.tasks.purge_runs", "schedule": crontab(minute=30, hour=3)},
    "sync-cmdb": {"task": "inventory.tasks.sync_all_cmdb", "schedule": crontab(minute=45, hour="*/6")},
    "daily-history": {"task": "inventory.tasks.daily_history", "schedule": crontab(minute=30, hour=23)},
    "purge-history": {"task": "inventory.tasks.purge_history", "schedule": crontab(minute=0, hour=4)},
}

# Classeurs Excel d'historique (volume partagé entre web et worker, disque de données dédié)
HISTORY_DIR = env("HISTORY_DIR", "/data/history")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "std"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"pyVmomi": {"level": "WARNING"}, "ldap3": {"level": "WARNING"}, "paramiko": {"level": "WARNING"}},
}
