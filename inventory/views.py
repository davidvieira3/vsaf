import math
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils import timezone

from core.models import AppSettings

from .exports import export_csv, export_workbook, export_xlsx
from .grids import GRIDS, LINK_FILTERS, LINK_LABELS, filter_options, filtered_queryset, resolve_grid
from .models import CollectionRun, Cluster, CustomField, Datastore, Host, Source, StorageSystem, VirtualMachine

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
    grid = resolve_grid(key)
    if grid is None:
        raise Http404
    columns = [
        {"field": c.field, "title": c.title, "type": c.type, "visible": c.visible, "width": c.width, "frozen": c.frozen}
        for c in grid.columns
    ]
    storage = grid.scope == "storage"
    pickers = []
    if not storage:
        pickers.append({"key": "source", "label": LINK_LABELS["source"]})
        pickers += [{"key": k, "label": LINK_LABELS[k]} for k in LINK_FILTERS.get(key, []) if k != "vm"]
        pickers += [{"key": f"cf_{cf.key}", "label": cf.label} for cf in CustomField.objects.all()]
    sources = StorageSystem.objects.only("id", "name") if storage else Source.objects.only("id", "name", "platform")
    return render(request, "inventory/grid.html", {
        "grid": grid,
        "config": {
            "key": key, "columns": columns, "thresholds": _thresholds(), "sort": grid.default_sort,
            "pickers": pickers, "vmFilter": (not storage) and "vm" in LINK_FILTERS.get(key, []),
            "sourceNames": {} if storage else {str(s.pk): s.name for s in sources},
        },
        "sources": sources,
        "storage": storage,
    })


def grid_options(request, kind):
    """Valeurs proposées par les filtres croisés, restreintes par les filtres déjà posés en amont."""
    return JsonResponse({"options": filter_options(kind, request.GET)})


def _json_safe(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def grid_data(request, key):
    grid = resolve_grid(key)
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
    fields.pop("custom", None)
    fields.pop("Champs personnalisés", None)
    extra = dict(getattr(obj, "props", None) or getattr(obj, "info", None) or {})
    source = obj if isinstance(obj, Source) else getattr(obj, "source", None)
    if source is not None:
        labels = {cf.key: cf.label for cf in CustomField.objects.all()}
        tags = {labels[k]: v for k, v in (source.custom or {}).items() if k in labels and v}
        if not isinstance(obj, Source):
            fields["Commentaire de la source"] = source.comment
        for label, value in tags.items():
            fields[label] = value
    return JsonResponse({"title": str(obj), "fields": fields, "extra": extra, "links": related_links(key, obj)},
                        json_dumps_params={"default": str})


def related_links(key, obj):
    """Liens « voir aussi » : ouvre une autre vue déjà filtrée sur l'objet affiché."""
    def url(view, **params):
        return reverse("inventory:grid", args=[view]) + "?" + urlencode(params, doseq=True)

    if key in ("arrays", "pools", "volumes"):
        sid = obj.pk if key == "arrays" else obj.system_id
        out = [("Volumes de la baie", url("volumes", source=sid)), ("Pools de la baie", url("pools", source=sid))]
        return [{"label": a, "url": b} for a, b in out]
    sid = obj.pk if key == "sources" else obj.source_id
    q = lambda name: f"{sid}:{name}"  # noqa: E731
    out = []
    if key == "sources":
        out = [(label, url(view, source=sid)) for view, label in (
            ("clusters", "Clusters"), ("hosts", "Hôtes"), ("datastores", "Datastores"), ("vms", "VM"),
            ("switches", "Commutateurs virtuels"), ("portgroups", "Portgroups"), ("neighbors", "CDP / LLDP"))]
    elif key == "clusters":
        c = q(obj.name)
        out = [("Hôtes du cluster", url("hosts", cluster=c)), ("VM du cluster", url("vms", cluster=c)),
               ("Datastores du cluster", url("datastores", cluster=c)), ("CDP / LLDP du cluster", url("neighbors", cluster=c))]
    elif key == "hosts":
        h = q(obj.name)
        out = [("VM sur cet hôte", url("vms", host=h)), ("Datastores montés", url("datastores", host=h)),
               ("CDP / LLDP", url("neighbors", host=h)), ("Commutateurs virtuels", url("switches", host=h)),
               ("Portgroups", url("portgroups", host=h))]
        if obj.cluster_name:
            out.append(("Son cluster", url("clusters", cluster=q(obj.cluster_name))))
    elif key == "datastores":
        d = q(obj.name)
        out = [("VM sur ce datastore", url("vms", datastore=d)), ("Hôtes qui le montent", url("hosts", datastore=d))]
        if obj.array_name:
            out.append(("Volume sur la baie", url("volumes", q=obj.array_volume.split(",")[0].split(":")[-1].strip())))
    elif key == "vms":
        out = [("Ses datastores", url("datastores", source=sid, vm=obj.name))]
        if obj.host_name:
            out.append(("Son hôte", url("hosts", host=q(obj.host_name))))
            out.append(("VM du même hôte", url("vms", host=q(obj.host_name))))
        if obj.cluster_name:
            out.append(("Son cluster", url("clusters", cluster=q(obj.cluster_name))))
    elif key in ("switches", "portgroups"):
        if obj.host_name:
            out.append(("Son hôte", url("hosts", host=q(obj.host_name))))
    elif key == "neighbors":
        out = [("Son hôte", url("hosts", host=q(obj.name)))]
        if obj.switch_name:
            out.append(("Tout ce qui est branché sur ce switch", url("neighbors", q=obj.switch_name)))
    if key != "sources":
        out.append(("Sa source", url("sources", source=sid)))
    return [{"label": a, "url": b} for a, b in out]


def grid_export(request, key, fmt):
    grid = resolve_grid(key)
    if grid is None:
        raise Http404
    return export_xlsx(grid, request.GET) if fmt == "xlsx" else export_csv(grid, request.GET)


def workbook_export(request):
    return export_workbook(request.GET)
