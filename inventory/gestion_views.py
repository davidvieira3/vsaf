"""Administration complète depuis le navigateur : sources, collectes, connecteurs, paramètres, journal."""
from celery import current_app
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.roles import role_required
from core.audit import audit
from core.models import AppSettings, AuditLog

from .collectors import collector_for
from .collectors.base import RunLog
from django.http import FileResponse, Http404

from . import history
from .cmdb import test_cmdb
from .forms import (
    CmdbConnectorForm, ConnectorForm, CustomFieldForm, SettingsForm, SourceForm, SourceImportForm, StorageSystemForm,
)
from .hardware import FETCHERS
from .models import (
    CmdbConnector, CmdbRecord, CollectionRun, CustomField, ExportFile, HardwareConnector, HardwareRecord, Source, StorageSystem,
)
from .storage.sync import test_storage
from .tasks import enqueue_cmdb, enqueue_connector, enqueue_source, enqueue_storage, manual_history


# --- Sources ------------------------------------------------------------------
@role_required("operator")
def sources_list(request):
    qs = Source.objects.all()
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(host__icontains=q) | Q(site__icontains=q))
    platform = request.GET.get("platform")
    if platform:
        qs = qs.filter(platform=platform)
    return render(request, "gestion/sources_list.html", {"sources": qs, "q": q, "platform": platform})


@role_required("admin")
def source_edit(request, pk=None):
    source = get_object_or_404(Source, pk=pk) if pk else None
    initial = {} if source else {"collect_interval_min": AppSettings.get().default_collect_interval_min}
    form = SourceForm(request.POST or None, instance=source, initial=initial)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit(request, "source-save", obj.name)
        messages.success(request, f"Source {obj.name} enregistrée.")
        if "save_test" in request.POST:
            return redirect("gestion:source_test", pk=obj.pk)
        return redirect("gestion:sources")
    groups = [
        ("Connexion", "", ["name", "platform", "host", "port", "username", "secret", "token_id", "verify_ssl"]),
        ("Classement et collecte", "", ["site", "description", "comment", "enabled", "collect_interval_min", "collect_network_hints", "collect_vm_devices"]),
        ("Proxmox : enrichissement matériel par SSH (facultatif)",
         "L'API Proxmox ne fournit ni numéro de série, ni IP du BMC, ni voisins LLDP. L'application peut les lire "
         "elle-même par SSH (dmidecode, ipmitool, lldpctl) avec un compte dédié.",
         ["ssh_enabled", "ssh_username", "ssh_port", "ssh_secret"]),
    ]
    groups = [(legend, hint, [form[n] for n in names]) for legend, hint, names in groups]
    return render(request, "gestion/source_form.html", {"form": form, "source": source, "groups": groups})


@role_required("admin")
@require_POST
def source_delete(request, pk):
    source = get_object_or_404(Source, pk=pk)
    audit(request, "source-delete", source.name)
    source.delete()
    messages.success(request, f"Source {source.name} et son inventaire supprimés.")
    return redirect("gestion:sources")


@role_required("operator")
def source_test(request, pk):
    source = get_object_or_404(Source, pk=pk)
    log = RunLog()
    try:
        result = collector_for(source, log).test()
        ok, msg = True, result
    except Exception as exc:
        ok, msg = False, f"{exc.__class__.__name__} : {getattr(exc, 'msg', None) or exc}"
    audit(request, "source-test", source.name, msg)
    (messages.success if ok else messages.error)(request, f"{source.name} — {msg}")
    return redirect("gestion:sources")


@role_required("operator")
@require_POST
def source_collect(request, pk):
    source = get_object_or_404(Source, pk=pk)
    run = enqueue_source(source, triggered_by=request.user.get_username())
    audit(request, "source-collect", source.name)
    if run:
        messages.success(request, f"Collecte de {source.name} mise en file.")
        return redirect("gestion:run_detail", pk=run.pk)
    messages.info(request, f"Une collecte de {source.name} est déjà en cours.")
    return redirect("gestion:sources")


@role_required("operator")
@require_POST
def collect_all(request):
    who = request.user.get_username()
    n = sum(1 for s in Source.objects.filter(enabled=True) if enqueue_source(s, triggered_by=who))
    n += sum(1 for s in StorageSystem.objects.filter(enabled=True) if enqueue_storage(s, triggered_by=who))
    audit(request, "collect-all", detail=f"{n} source(s)")
    messages.success(request, f"{n} collecte(s) mise(s) en file.")
    return redirect("gestion:runs")


@role_required("admin")
@require_POST
def source_toggle(request, pk):
    source = get_object_or_404(Source, pk=pk)
    source.enabled = not source.enabled
    source.save(update_fields=["enabled"])
    audit(request, "source-toggle", source.name, f"enabled={source.enabled}")
    return redirect("gestion:sources")


@role_required("admin")
def sources_import(request):
    form = SourceImportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rows, errors = form.parsed()
        interval = AppSettings.get().default_collect_interval_min
        with transaction.atomic():
            created = updated = 0
            for r in rows:
                secret = r.pop("secret")
                obj, is_new = Source.objects.get_or_create(name=r["name"], defaults={**r, "collect_interval_min": interval})
                if not is_new:
                    for k, v in r.items():
                        setattr(obj, k, v)
                obj.verify_ssl = form.cleaned_data["verify_ssl"]
                obj.secret = secret
                obj.save()
                created += is_new
                updated += not is_new
        audit(request, "sources-import", detail=f"{created} créée(s), {updated} mise(s) à jour")
        for e in errors:
            messages.warning(request, e)
        messages.success(request, f"{created} source(s) créée(s), {updated} mise(s) à jour.")
        return redirect("gestion:sources")
    return render(request, "gestion/form.html", {"form": form, "title": "Importer des sources en masse", "submit": "Importer", "back": "gestion:sources"})


# --- Historique des collectes -------------------------------------------------
@role_required("operator")
def runs_list(request):
    qs = CollectionRun.objects.select_related("source", "connector", "storage_system", "cmdb")
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    return render(request, "gestion/runs_list.html", {"runs": qs[:300], "status": status, "statuses": CollectionRun.STATUS})


@role_required("operator")
def run_detail(request, pk):
    run = get_object_or_404(CollectionRun.objects.select_related("source", "connector", "storage_system", "cmdb"), pk=pk)
    if request.GET.get("format") == "json":
        return JsonResponse({"status": run.status, "log": run.log, "counts": run.counts,
                             "finished": run.finished_at.isoformat() if run.finished_at else None})
    return render(request, "gestion/run_detail.html", {"run": run})


# --- Connecteurs matériel -----------------------------------------------------
@role_required("operator")
def connectors_list(request):
    return render(request, "gestion/connectors_list.html", {
        "connectors": HardwareConnector.objects.all(),
        "records": HardwareRecord.objects.count(),
    })


@role_required("admin")
def connector_edit(request, pk=None):
    conn = get_object_or_404(HardwareConnector, pk=pk) if pk else None
    form = ConnectorForm(request.POST or None, instance=conn)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit(request, "connector-save", obj.name)
        messages.success(request, f"Connecteur {obj.name} enregistré.")
        return redirect("gestion:connectors")
    return render(request, "gestion/form.html", {"form": form, "title": conn and f"Connecteur {conn.name}" or "Nouveau connecteur matériel",
                                                 "submit": "Enregistrer", "back": "gestion:connectors"})


@role_required("operator")
@require_POST
def connector_sync(request, pk):
    conn = get_object_or_404(HardwareConnector, pk=pk)
    run = enqueue_connector(conn, triggered_by=request.user.get_username())
    audit(request, "connector-sync", conn.name)
    if run:
        return redirect("gestion:run_detail", pk=run.pk)
    messages.info(request, "Synchronisation déjà en cours.")
    return redirect("gestion:connectors")


@role_required("admin")
@require_POST
def connector_delete(request, pk):
    conn = get_object_or_404(HardwareConnector, pk=pk)
    audit(request, "connector-delete", conn.name)
    conn.delete()
    messages.success(request, "Connecteur supprimé.")
    return redirect("gestion:connectors")


@role_required("operator")
def connector_test(request, pk):
    conn = get_object_or_404(HardwareConnector, pk=pk)
    log = RunLog()
    try:
        if conn.kind == "redfish":
            from .hardware.base import expand_hosts
            from .hardware.redfish import _one
            first = expand_hosts(conn.bmc_hosts)[0]
            rec = _one(conn, first)
            msg = f"{first} répond : {rec['vendor']} {rec['model']} n° {rec['serial']}"
        else:
            records = FETCHERS[conn.kind](conn, log)
            msg = f"{len(records)} équipement(s) lus"
        messages.success(request, f"{conn.name} — {msg}")
    except Exception as exc:
        messages.error(request, f"{conn.name} — {exc.__class__.__name__} : {exc}")
    return redirect("gestion:connectors")


# --- Paramètres, journal, système ------------------------------------------
@role_required("admin")
def settings_edit(request):
    form = SettingsForm(request.POST or None, instance=AppSettings.get())
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(request, "settings-save")
        messages.success(request, "Paramètres enregistrés.")
        return redirect("gestion:settings")
    return render(request, "gestion/form.html", {"form": form, "title": "Paramètres", "submit": "Enregistrer"})


@role_required("admin")
def audit_list(request):
    qs = AuditLog.objects.all()
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(action__icontains=q) | Q(target__icontains=q))
    return render(request, "gestion/audit_list.html", {"entries": qs[:500], "q": q})


@role_required("admin")
def system_status(request):
    workers, error = {}, None
    try:
        insp = current_app.control.inspect(timeout=2.0)
        ping = insp.ping() or {}
        active = insp.active() or {}
        stats = insp.stats() or {}
        for name in ping:
            workers[name] = {
                "active": len(active.get(name, [])),
                "concurrency": (stats.get(name, {}).get("pool") or {}).get("max-concurrency"),
            }
    except Exception as exc:
        error = str(exc)
    queued = CollectionRun.objects.filter(status="queued").count()
    running = CollectionRun.objects.filter(status="running").select_related("source", "connector")
    return render(request, "gestion/system.html", {"workers": workers, "error": error, "queued": queued, "running": running,
                                                   "now": timezone.now()})


@role_required("admin")
@require_POST
def cancel_queued(request):
    n = CollectionRun.objects.filter(status="queued").update(status="error", finished_at=timezone.now(), log="Annulée depuis l'interface.")
    audit(request, "cancel-queued", detail=str(n))
    messages.success(request, f"{n} collecte(s) en file annulée(s).")
    return redirect("gestion:system")


# --- Baies de stockage ----------------------------------------------------------
@role_required("operator")
def storage_list(request):
    return render(request, "gestion/storage_list.html", {"systems": StorageSystem.objects.all()})


@role_required("admin")
def storage_edit(request, pk=None):
    system = get_object_or_404(StorageSystem, pk=pk) if pk else None
    form = StorageSystemForm(request.POST or None, instance=system)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit(request, "storage-save", obj.name)
        messages.success(request, f"Baie {obj.name} enregistrée.")
        return redirect("gestion:storage")
    return render(request, "gestion/form.html", {
        "form": form, "title": system and f"Baie {system.name}" or "Nouvelle baie de stockage", "submit": "Enregistrer", "back": "gestion:storage",
        "extra": "Comptes conseillés : 3PAR/Primera/Alletra 9000 rôle « browse » ; Nimble/Alletra 6000 rôle « guest » ; "
                 "ONTAP rôle « readonly » ; Pure utilisateur « readonly » et son jeton API.",
    })


@role_required("operator")
def storage_test(request, pk):
    system = get_object_or_404(StorageSystem, pk=pk)
    try:
        messages.success(request, f"{system.name} — {test_storage(system, RunLog())}")
    except Exception as exc:
        messages.error(request, f"{system.name} — {exc.__class__.__name__} : {exc}")
    audit(request, "storage-test", system.name)
    return redirect("gestion:storage")


@role_required("operator")
@require_POST
def storage_collect(request, pk):
    system = get_object_or_404(StorageSystem, pk=pk)
    run = enqueue_storage(system, triggered_by=request.user.get_username())
    audit(request, "storage-collect", system.name)
    if run:
        return redirect("gestion:run_detail", pk=run.pk)
    messages.info(request, "Collecte déjà en cours.")
    return redirect("gestion:storage")


@role_required("admin")
@require_POST
def storage_delete(request, pk):
    system = get_object_or_404(StorageSystem, pk=pk)
    audit(request, "storage-delete", system.name)
    system.delete()
    messages.success(request, "Baie supprimée.")
    return redirect("gestion:storage")


# --- ServiceNow -------------------------------------------------------------
@role_required("operator")
def cmdb_list(request):
    from inventory.models import VirtualMachine
    return render(request, "gestion/cmdb_list.html", {
        "connectors": CmdbConnector.objects.all(),
        "records": CmdbRecord.objects.count(),
        "vms": VirtualMachine.objects.filter(template=False).count(),
        "matched": VirtualMachine.objects.filter(template=False).exclude(cmdb_sys_id="").count(),
    })


@role_required("admin")
def cmdb_edit(request, pk=None):
    conn = get_object_or_404(CmdbConnector, pk=pk) if pk else None
    form = CmdbConnectorForm(request.POST or None, instance=conn)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit(request, "cmdb-save", obj.name)
        messages.success(request, f"Connecteur {obj.name} enregistré.")
        return redirect("gestion:cmdb")
    return render(request, "gestion/form.html", {"form": form, "title": conn and f"ServiceNow {conn.name}" or "Nouveau connecteur ServiceNow",
                                                 "submit": "Enregistrer", "back": "gestion:cmdb"})


@role_required("operator")
def cmdb_test(request, pk):
    conn = get_object_or_404(CmdbConnector, pk=pk)
    try:
        messages.success(request, f"{conn.name} — {test_cmdb(conn, RunLog())}")
    except Exception as exc:
        messages.error(request, f"{conn.name} — {exc.__class__.__name__} : {exc}")
    audit(request, "cmdb-test", conn.name)
    return redirect("gestion:cmdb")


@role_required("operator")
@require_POST
def cmdb_sync(request, pk):
    conn = get_object_or_404(CmdbConnector, pk=pk)
    run = enqueue_cmdb(conn, triggered_by=request.user.get_username())
    audit(request, "cmdb-sync", conn.name)
    if run:
        return redirect("gestion:run_detail", pk=run.pk)
    messages.info(request, "Synchronisation déjà en cours.")
    return redirect("gestion:cmdb")


@role_required("admin")
@require_POST
def cmdb_delete(request, pk):
    conn = get_object_or_404(CmdbConnector, pk=pk)
    audit(request, "cmdb-delete", conn.name)
    conn.delete()
    from .cmdb import apply_cmdb
    apply_cmdb()
    messages.success(request, "Connecteur supprimé ; les colonnes CMDB des VM ont été vidées.")
    return redirect("gestion:cmdb")


# --- Historique Excel (lecture pour tous les rôles) -------------------------------
def history_list(request):
    qs = ExportFile.objects.select_related("source", "storage_system")
    kind = request.GET.get("kind", "")
    q = request.GET.get("q", "").strip()
    day_from, day_to = request.GET.get("from", ""), request.GET.get("to", "")
    if kind:
        qs = qs.filter(kind=kind)
    if q:
        qs = qs.filter(label__icontains=q)
    if day_from:
        qs = qs.filter(created_at__date__gte=day_from)
    if day_to:
        qs = qs.filter(created_at__date__lte=day_to)
    used, free = history.disk_usage()
    return render(request, "gestion/history_list.html", {
        "files": qs[:500], "count": qs.count(), "kinds": ExportFile.KINDS, "kind": kind, "q": q,
        "day_from": day_from, "day_to": day_to, "used": used, "free": free,
        "retention": AppSettings.get().history_retention_days,
    })


def history_download(request, pk):
    export = get_object_or_404(ExportFile, pk=pk)
    try:
        path = history.resolve(export)
    except PermissionError:
        raise Http404
    if not path.exists():
        raise Http404("Fichier absent du disque (purgé ou volume non monté).")
    audit(request, "history-download", export.label, export.path)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)


@role_required("operator")
@require_POST
def history_generate(request):
    manual_history.delay()
    audit(request, "history-generate")
    messages.success(request, "Classeur complet en cours de génération ; il apparaîtra dans la liste d'ici quelques minutes.")
    return redirect("gestion:history")


@role_required("admin")
@require_POST
def history_delete(request, pk):
    export = get_object_or_404(ExportFile, pk=pk)
    try:
        history.resolve(export).unlink(missing_ok=True)
    except PermissionError:
        pass
    audit(request, "history-delete", export.label, export.path)
    export.delete()
    messages.success(request, "Fichier supprimé.")
    return redirect("gestion:history")


# --- Commentaires et champs personnalisés des sources ------------------------------
@role_required("operator")
def annotations(request):
    """Tableau unique pour commenter et catégoriser toutes les sources d'un coup."""
    fields = list(CustomField.objects.all())
    qs = Source.objects.all()
    platform = request.GET.get("platform", "")
    q = request.GET.get("q", "").strip()
    if platform:
        qs = qs.filter(platform=platform)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(comment__icontains=q) | Q(site__icontains=q))
    sources = list(qs)
    if request.method == "POST":
        changed = 0
        for s in sources:
            if f"c_{s.pk}" not in request.POST:
                continue
            comment = request.POST.get(f"c_{s.pk}", "").strip()[:2000]
            custom = dict(s.custom or {})
            for cf in fields:
                value = request.POST.get(f"f_{s.pk}_{cf.key}", "").strip()[:255]
                if cf.kind == "choice" and value and value not in cf.choice_list():
                    value = custom.get(cf.key, "")  # valeur hors liste : on garde l'ancienne
                if value:
                    custom[cf.key] = value
                else:
                    custom.pop(cf.key, None)
            if comment != s.comment or custom != (s.custom or {}):
                s.comment, s.custom = comment, custom
                s.save(update_fields=["comment", "custom"])
                changed += 1
        audit(request, "annotations-save", detail=f"{changed} source(s) modifiée(s)")
        messages.success(request, f"{changed} source(s) mise(s) à jour.")
        return redirect(request.get_full_path())
    rows = [
        {"source": s, "cells": [{"field": cf, "value": (s.custom or {}).get(cf.key, ""), "name": f"f_{s.pk}_{cf.key}"} for cf in fields]}
        for s in sources
    ]
    return render(request, "gestion/annotations.html", {"rows": rows, "fields": fields, "platform": platform, "q": q})


@role_required("admin")
def fields_list(request):
    return render(request, "gestion/fields_list.html", {"fields": CustomField.objects.all()})


@role_required("admin")
def field_edit(request, pk=None):
    cf = get_object_or_404(CustomField, pk=pk) if pk else None
    form = CustomFieldForm(request.POST or None, instance=cf)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit(request, "customfield-save", obj.key)
        messages.success(request, f"Champ « {obj.label} » enregistré. Il apparaît comme colonne et comme filtre dans toutes les vues.")
        return redirect("gestion:fields")
    return render(request, "gestion/form.html", {"form": form, "title": cf and f"Champ {cf.label}" or "Nouveau champ personnalisé",
                                                 "submit": "Enregistrer", "back": "gestion:fields"})


@role_required("admin")
@require_POST
def field_delete(request, pk):
    cf = get_object_or_404(CustomField, pk=pk)
    with transaction.atomic():
        for s in Source.objects.exclude(custom={}):
            if cf.key in (s.custom or {}):
                s.custom.pop(cf.key)
                s.save(update_fields=["custom"])
        cf.delete()
    audit(request, "customfield-delete", cf.key)
    messages.success(request, f"Champ « {cf.label} » supprimé, ainsi que ses valeurs.")
    return redirect("gestion:fields")
