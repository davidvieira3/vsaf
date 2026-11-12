"""Historique des collectes sous forme de classeurs Excel, conservés N jours (183 par défaut ≈ 6 mois)."""
import logging
import os
import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from core.models import AppSettings

from .exports import INVENTORY_SHEETS, SHEETS, STORAGE_SHEETS, build_workbook
from .models import ExportFile

log = logging.getLogger(__name__)


def root() -> Path:
    path = Path(settings.HISTORY_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:80] or "sans-nom"


def _write(kind, label, params, sheets, **links):
    now = timezone.localtime()
    rel = Path(f"{now:%Y/%m/%d}") / f"{_slug(label)}_{now:%Y%m%d-%H%M%S}.xlsx"
    full = root() / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    tmp = full.with_suffix(".tmp")
    build_workbook(params, sheets).save(tmp)
    os.replace(tmp, full)  # pas de fichier à moitié écrit visible dans l'interface
    return ExportFile.objects.create(kind=kind, label=label, path=str(rel), size_bytes=full.stat().st_size, **links)


def snapshot_source(source, run=None):
    if not AppSettings.get().history_per_collection:
        return None
    return _write("source", source.name, {"source": str(source.pk)}, INVENTORY_SHEETS, source=source, run=run)


def snapshot_storage(system, run=None):
    if not AppSettings.get().history_per_collection:
        return None
    return _write("storage", system.name, {"source": str(system.pk)}, STORAGE_SHEETS, storage_system=system, run=run)


def snapshot_all(kind="daily"):
    return _write(kind, "inventaire-complet", {}, SHEETS)


def resolve(export: ExportFile) -> Path:
    """Chemin absolu, en refusant toute sortie du répertoire d'historique."""
    base = root().resolve()
    full = (base / export.path).resolve()
    if base not in full.parents:
        raise PermissionError("Chemin hors du répertoire d'historique")
    return full


def purge():
    days = AppSettings.get().history_retention_days
    limit = timezone.now() - timedelta(days=days)
    n = 0
    for export in ExportFile.objects.filter(created_at__lt=limit).iterator():
        try:
            resolve(export).unlink(missing_ok=True)
        except (OSError, PermissionError) as exc:
            log.warning("Suppression impossible de %s : %s", export.path, exc)
        export.delete()
        n += 1
    # répertoires vides laissés par la purge
    for dirpath, dirnames, filenames in os.walk(root(), topdown=False):
        if dirpath != str(root()) and not dirnames and not filenames:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
    return n


def disk_usage():
    total = 0
    for dirpath, _, filenames in os.walk(root()):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    try:
        st = os.statvfs(root())
        free = st.f_bavail * st.f_frsize
    except OSError:
        free = None
    return total, free
