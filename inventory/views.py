import math
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from core.models import AppSettings

from .exports import export_csv, export_workbook, export_xlsx
from .grids import GRIDS, filtered_queryset
from .models import CollectionRun, Cluster, Datastore, Host, Source, StorageSystem, VirtualMachine

NAV = [
    ("sources", "vCenter & Proxmox"),
    ("clusters", "Clusters"),
    ("hosts", "Hôtes"),
    ("datastores", "Stockage"),
    ("switches", "Commutateurs virtuels"),
    ("portgroups", "Portgroups"),
    ("neighbors", "CDP / LLDP"),
    ("vms", "Machines virtuelles"),
]


def _thresholds():
    s = AppSettings.get()
    return {
        "cpu": [s.cpu_ratio_warn, s.cpu_ratio_crit],
        "mem": [s.mem_ratio_warn, s.mem_ratio_crit],
        "ds": [s.ds_used_warn, s.ds_used_crit],
    }


def dashboard(request):
    st = AppSettings.get()
    sources = Source.objects.all()
    by_platform = {}
    for p in ("vmware", "proxmox"):
        agg = sources.filter(platform=p).aggregate(
            n=Count("id"), hosts=Sum("num_hosts"), vms=Sum("num_vms"), vms_on=Sum("num_vms_on"),
            cores=Sum("total_cores"), vcpu=Sum("vcpu_on"), mem=Sum("total_mem_bytes"), vram=Sum("vram_on_bytes"),
            cap=Sum("storage_capacity_bytes"), free=Sum("storage_free_bytes"),
        )
        agg = {k: v or 0 for k, v in agg.items()}
        agg["cpu_ratio"] = round(agg["vcpu"] / agg["cores"], 2) if agg["cores"] else None
        agg["mem_ratio"] = round(agg["vram"] / agg["mem"], 2) if agg["mem"] else None
        agg["used_pct"] = round(100 * (agg["cap"] - agg["free"]) / agg["cap"], 1) if agg["cap"] else None
        by_platform[p] = agg
    ctx = {
        "by_platform": by_platform,
        "failing": sources.filter(enabled=True, last_status="error").order_by("name")[:20],
        "stale": sources.filter(enabled=True, last_success_at__lt=timezone.now() - timedelta(days=1)).count(),
        "never": sources.filter(enabled=True, last_success_at__isnull=True).count(),
        "running": CollectionRun.objects.filter(status__in=("queued", "running")).count(),
        "hot_clusters": Cluster.objects.select_related("source").filter(cpu_ratio_on__isnull=False).order_by("-cpu_ratio_on")[:10],
        "hot_mem": Cluster.objects.select_related("source").filter(mem_ratio_on__isnull=False).order_by("-mem_ratio_on")[:10],
        "full_ds": Datastore.objects.select_related("source").filter(used_pct__gte=st.ds_used_warn).order_by("-used_pct")[:10],
        "hosts_down": Host.objects.select_related("source").exclude(connection_state__in=("connected", "online")).order_by("name")[:20],
        "no_bmc": Host.objects.filter(bmc_ip="").count(),
        "templates": VirtualMachine.objects.filter(template=True).count(),
        "full_arrays": StorageSystem.objects.filter(used_pct__gte=st.ds_used_warn).order_by("-used_pct")[:10],
        "cmdb_missing": VirtualMachine.objects.filter(template=False, cmdb_sys_id="").count(),
        "settings": st,
    }
    return render(request, "inventory/dashboard.html", ctx)


def grid_page(request, key):
    grid = GRIDS.get(key)
    if grid is None:
        raise Http404
    columns = [
        {"field": c.field, "title": c.title, "type": c.type, "visible": c.visible, "width": c.width, "frozen": c.frozen}
        for c in grid.columns
    ]
    return render(request, "inventory/grid.html", {
        "grid": grid,
        "config": {"key": key, "columns": columns, "thresholds": _thresholds(), "sort": grid.default_sort},
        "sources": StorageSystem.objects.only("id", "name") if grid.scope == "storage" else Source.objects.only("id", "name", "platform"),
    })


def _json_safe(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def grid_data(request, key):
    grid = GRIDS.get(key)
    if grid is None:
        raise Http404
    qs = filtered_queryset(grid, request.GET)
    try:
        size = max(10, min(int(request.GET.get("size", 100)), 1000))
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        size, page = 100, 1
    total = qs.count()
    rows = list(qs.values("pk", *grid.fields)[(page - 1) * size: page * size])
    data = [{k: _json_safe(v) for k, v in r.items()} for r in rows]
    return JsonResponse({"last_page": max(1, math.ceil(total / size)), "last_row": total, "data": data})


def grid_detail(request, key, pk):
    grid = GRIDS.get(key)
    if grid is None:
        raise Http404
    obj = get_object_or_404(grid.model, pk=pk)
    fields = {}
    for f in grid.model._meta.concrete_fields:
        if f.name.endswith("_enc") or f.name in ("props", "info"):
            continue
        v = getattr(obj, f.attname)
        if f.is_relation:
            v = str(getattr(obj, f.name))
        fields[f.verbose_name if f.verbose_name != f.name.replace("_", " ") else f.name] = _json_safe(v)
    extra = getattr(obj, "props", None) or getattr(obj, "info", None) or {}
    return JsonResponse({"title": str(obj), "fields": fields, "extra": extra}, json_dumps_params={"default": str})


def grid_export(request, key, fmt):
    grid = GRIDS.get(key)
    if grid is None:
        raise Http404
    return export_xlsx(grid, request.GET) if fmt == "xlsx" else export_csv(grid, request.GET)


def workbook_export(request):
    return export_workbook(request.GET)
