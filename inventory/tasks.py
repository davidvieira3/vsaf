import logging
import time
from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from core.models import AppSettings

from .collectors import collector_for
from .collectors.base import RunLog, persist
from . import history
from .cmdb import apply_cmdb, sync_cmdb
from .hardware.sync import apply_hardware, sync_connector
from .models import CmdbConnector, CollectionRun, HardwareConnector, Host, Source, StorageSystem, VirtualMachine
from .storage.sync import collect_storage, map_datastores

logger = logging.getLogger(__name__)
LOCK_TTL = 3 * 3600


def enqueue_source(source, triggered_by="planificateur"):
    """Crée l'entrée d'historique et poste la tâche. Refuse si une collecte est déjà en cours/en file."""
    if CollectionRun.objects.filter(source=source, status__in=("queued", "running")).exists():
        return None
    run = CollectionRun.objects.create(kind="source", source=source, triggered_by=triggered_by)
    collect_source.delay(run.pk)
    return run


def enqueue_storage(system, triggered_by="planificateur"):
    if CollectionRun.objects.filter(storage_system=system, status__in=("queued", "running")).exists():
        return None
    run = CollectionRun.objects.create(kind="storage", storage_system=system, triggered_by=triggered_by)
    collect_storage_task.delay(run.pk)
    return run


def enqueue_cmdb(conn, triggered_by="planificateur"):
    if CollectionRun.objects.filter(cmdb=conn, status__in=("queued", "running")).exists():
        return None
    run = CollectionRun.objects.create(kind="cmdb", cmdb=conn, triggered_by=triggered_by)
    sync_cmdb_task.delay(run.pk)
    return run


def enqueue_connector(connector, triggered_by="planificateur"):
    if CollectionRun.objects.filter(connector=connector, status__in=("queued", "running")).exists():
        return None
    run = CollectionRun.objects.create(kind="hardware", connector=connector, triggered_by=triggered_by)
    sync_hardware.delay(run.pk)
    return run


@shared_task(bind=True, acks_late=True, soft_time_limit=2 * 3600, time_limit=2 * 3600 + 300)
def collect_source(self, run_id):
    run = CollectionRun.objects.select_related("source").get(pk=run_id)
    if run.status != "queued":  # annulée depuis l'interface entre-temps
        return
    source = run.source
    lock = f"collect-lock:{source.pk}"
    if not cache.add(lock, run_id, LOCK_TTL):
        run.status, run.log, run.finished_at = "error", "Une autre collecte de cette source est déjà en cours.", timezone.now()
        run.save()
        return
    log = RunLog(run)
    t0 = time.monotonic()
    run.status, run.started_at = "running", timezone.now()
    run.save(update_fields=["status", "started_at"])
    Source.objects.filter(pk=source.pk).update(last_status="running", last_run_at=run.started_at)
    try:
        log.info(f"Collecte de {source.name} ({source.get_platform_display()}, {source.host}:{source.port})")
        result, about = collector_for(source, log).collect()
        log.info("Enregistrement en base...")
        source.refresh_from_db()
        counts = persist(source, result, about)
        apply_hardware(Host.objects.filter(source=source).exclude(serial=""))
        apply_cmdb(VirtualMachine.objects.filter(source=source).select_related("source"))
        map_datastores()
        try:
            export = history.snapshot_source(source, run)
            if export:
                log.info(f"Historique Excel : {export.path}")
        except Exception as exc:
            log.warn(f"Fichier Excel d'historique non écrit : {exc}")
        run.counts = counts
        run.status = "partial" if log.warnings else "ok"
        source.last_status, source.last_error = run.status, ""
        source.last_success_at = timezone.now()
        log.info("Terminé : " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    except SoftTimeLimitExceeded:
        log.error("Durée maximale dépassée (2 h) : collecte interrompue")
        run.status, source.last_status, source.last_error = "error", "error", "Délai dépassé"
    except Exception as exc:
        logger.exception("Collecte %s en échec", source.name)
        log.error(f"{exc.__class__.__name__} : {getattr(exc, 'msg', None) or exc}")
        run.status, source.last_status = "error", "error"
        source.last_error = f"{exc.__class__.__name__} : {getattr(exc, 'msg', None) or exc}"[:2000]
    finally:
        cache.delete(lock)
        run.finished_at = timezone.now()
        run.log = "\n".join(log.lines)
        run.save()
        source.last_duration_s = round(time.monotonic() - t0, 1)
        source.next_run_at = timezone.now() + timedelta(minutes=source.collect_interval_min or 240)
        source.save(update_fields=["last_status", "last_error", "last_success_at", "last_duration_s", "next_run_at"])


@shared_task(bind=True, acks_late=True, soft_time_limit=3600, time_limit=3900)
def sync_hardware(self, run_id):
    run = CollectionRun.objects.select_related("connector").get(pk=run_id)
    if run.status != "queued":
        return
    conn = run.connector
    log = RunLog(run)
    run.status, run.started_at = "running", timezone.now()
    run.save(update_fields=["status", "started_at"])
    try:
        log.info(f"Synchronisation matériel : {conn.name} ({conn.get_kind_display()})")
        run.counts = sync_connector(conn, log)
        run.status = "partial" if log.warnings else "ok"
        conn.last_status, conn.last_error = run.status, ""
    except Exception as exc:
        logger.exception("Connecteur %s en échec", conn.name)
        log.error(f"{exc.__class__.__name__} : {exc}")
        run.status = conn.last_status = "error"
        conn.last_error = str(exc)[:2000]
    finally:
        run.finished_at = conn.last_run_at = timezone.now()
        run.log = "\n".join(log.lines)
        run.save()
        conn.save(update_fields=["last_status", "last_error", "last_run_at"])


def _run_simple(run, label, work, on_ok=None):
    """Squelette commun : statut, journal, erreurs."""
    log = RunLog(run)
    run.status, run.started_at = "running", timezone.now()
    run.save(update_fields=["status", "started_at"])
    try:
        log.info(label)
        run.counts = work(log)
        run.status = "partial" if log.warnings else "ok"
        if on_ok:
            on_ok(run, log)
    except Exception as exc:
        logger.exception("%s en échec", label)
        log.error(f"{exc.__class__.__name__} : {exc}")
        run.status = "error"
    finally:
        run.finished_at = timezone.now()
        run.log = "\n".join(log.lines)
        run.save()
    return run, log


@shared_task(bind=True, acks_late=True, soft_time_limit=3600, time_limit=3900)
def collect_storage_task(self, run_id):
    run = CollectionRun.objects.select_related("storage_system").get(pk=run_id)
    if run.status != "queued":
        return
    system = run.storage_system

    def snapshot(run, log):
        try:
            export = history.snapshot_storage(system, run)
            if export:
                log.info(f"Historique Excel : {export.path}")
        except Exception as exc:
            log.warn(f"Fichier Excel d'historique non écrit : {exc}")

    run, log = _run_simple(run, f"Collecte de la baie {system.name} ({system.get_kind_display()})",
                           lambda log: collect_storage(system, log), snapshot)
    system.refresh_from_db()
    system.last_status = run.status
    system.last_error = "" if run.status != "error" else (log.lines[-1] if log.lines else "")[:2000]
    system.last_run_at = run.finished_at
    system.next_run_at = timezone.now() + timedelta(minutes=system.collect_interval_min or 240)
    system.save(update_fields=["last_status", "last_error", "last_run_at", "next_run_at"])


@shared_task(bind=True, acks_late=True, soft_time_limit=3600, time_limit=3900)
def sync_cmdb_task(self, run_id):
    run = CollectionRun.objects.select_related("cmdb").get(pk=run_id)
    if run.status != "queued":
        return
    conn = run.cmdb
    run, log = _run_simple(run, f"Synchronisation ServiceNow : {conn.name} ({conn.table})", lambda log: sync_cmdb(conn, log))
    conn.last_status = run.status
    conn.last_error = "" if run.status != "error" else (log.lines[-1] if log.lines else "")[:2000]
    conn.last_run_at = run.finished_at
    conn.save(update_fields=["last_status", "last_error", "last_run_at"])


@shared_task
def sync_all_cmdb():
    for conn in CmdbConnector.objects.filter(enabled=True):
        enqueue_cmdb(conn)


@shared_task(soft_time_limit=3 * 3600, time_limit=3 * 3600 + 300)
def daily_history():
    from core.models import AppSettings
    if AppSettings.get().history_daily:
        history.snapshot_all("daily")


@shared_task(soft_time_limit=3 * 3600, time_limit=3 * 3600 + 300)
def manual_history():
    history.snapshot_all("manual")


@shared_task
def purge_history():
    return history.purge()


@shared_task
def dispatch_due():
    """Toutes les minutes : met en file les sources dont l'échéance est passée."""
    now = timezone.now()
    due = Source.objects.filter(enabled=True).filter(Q(next_run_at__isnull=True) | Q(next_run_at__lte=now))
    n = 0
    for source in due:
        if enqueue_source(source):
            n += 1
    for system in StorageSystem.objects.filter(enabled=True).filter(Q(next_run_at__isnull=True) | Q(next_run_at__lte=now)):
        if enqueue_storage(system):
            n += 1
    # filet de sécurité : collectes bloquées (worker redémarré pendant l'exécution)
    stale = now - timedelta(hours=3)
    CollectionRun.objects.filter(status__in=("queued", "running"), queued_at__lt=stale).update(
        status="error", finished_at=now, log="Abandonnée : aucun retour du worker depuis plus de 3 h."
    )
    return n


@shared_task
def sync_all_hardware():
    for conn in HardwareConnector.objects.filter(enabled=True):
        enqueue_connector(conn)


@shared_task
def purge_runs():
    days = AppSettings.get().run_retention_days
    deleted, _ = CollectionRun.objects.filter(queued_at__lt=timezone.now() - timedelta(days=days)).delete()
    return deleted
