"""Outils communs aux collecteurs : journal de collecte, agrégats de ratios, persistance."""
import time
from collections import defaultdict
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from inventory.models import (
    Cluster, Datacenter, Datastore, Host, InventoryLink, NetworkNeighbor, PortGroup, Source, VirtualMachine, VSwitch,
)

RESULT_MODELS = [
    ("datacenters", Datacenter),
    ("clusters", Cluster),
    ("hosts", Host),
    ("datastores", Datastore),
    ("vswitches", VSwitch),
    ("portgroups", PortGroup),
    ("neighbors", NetworkNeighbor),
    ("vms", VirtualMachine),
]


class RunLog:
    """Journal horodaté sauvegardé dans CollectionRun.log (visible en direct dans l'interface)."""

    def __init__(self, run=None, flush_every=5.0):
        self.run = run
        self.lines = []
        self.warnings = 0
        self.errors = 0
        self._last_flush = time.monotonic()
        self.flush_every = flush_every

    def _add(self, level, msg):
        self.lines.append(f"{datetime.now():%H:%M:%S} {level:<5} {msg}")
        if self.run is not None and time.monotonic() - self._last_flush > self.flush_every:
            self.flush()

    def info(self, msg):
        self._add("INFO", msg)

    def warn(self, msg):
        self.warnings += 1
        self._add("WARN", msg)

    def error(self, msg):
        self.errors += 1
        self._add("ERROR", msg)

    def flush(self):
        if self.run is not None:
            self.run.log = "\n".join(self.lines[-5000:])
            self.run.save(update_fields=["log"])
        self._last_flush = time.monotonic()


def ratio(num, den):
    return round(num / den, 2) if den else None


def pct(num, den):
    return round(100.0 * num / den, 1) if den else None


def s(value, maxlen=255):
    if value is None:
        return ""
    return str(value)[:maxlen]


class VmAggregate:
    """Additionne vCPU / vRAM par clé (hôte, cluster...) pour calculer les ratios de consolidation."""

    def __init__(self):
        self.data = defaultdict(lambda: {"vm_count": 0, "vm_on": 0, "vcpu_on": 0, "vcpu_all": 0, "vram_on_bytes": 0, "vram_all_bytes": 0})

    def add(self, key, vcpu, mem_bytes, powered_on):
        d = self.data[key]
        d["vm_count"] += 1
        d["vcpu_all"] += vcpu or 0
        d["vram_all_bytes"] += mem_bytes or 0
        if powered_on:
            d["vm_on"] += 1
            d["vcpu_on"] += vcpu or 0
            d["vram_on_bytes"] += mem_bytes or 0

    def fields(self, key, cores, mem_bytes):
        d = dict(self.data.get(key) or self.data.default_factory())
        d["cpu_ratio_on"] = ratio(d["vcpu_on"], cores)
        d["cpu_ratio_all"] = ratio(d["vcpu_all"], cores)
        d["mem_ratio_on"] = ratio(d["vram_on_bytes"], mem_bytes)
        d["mem_ratio_all"] = ratio(d["vram_all_bytes"], mem_bytes)
        return d


_FIELD_CACHE = {}


def _clean(model, row):
    """Ne garde que les champs du modèle et tronque les textes à leur longueur max (évite un rejet en base)."""
    meta = _FIELD_CACHE.get(model)
    if meta is None:
        fields = [f for f in model._meta.get_fields() if getattr(f, "concrete", False) and f.name not in ("id", "source")]
        meta = {f.name: getattr(f, "max_length", None) for f in fields}
        _FIELD_CACHE[model] = meta
    out = {}
    for key, value in row.items():
        if key not in meta:
            continue
        limit = meta[key]
        if limit and value is not None:
            value = str(value)[:limit]
        out[key] = value
    return out


def summarize(source, result, about):
    hosts = result.get("hosts", [])
    vms = [v for v in result.get("vms", []) if not v.get("template")]
    dss = result.get("datastores", [])
    cores = sum(h.get("cpu_cores") or 0 for h in hosts)
    mem = sum(h.get("mem_bytes") or 0 for h in hosts)
    agg = VmAggregate()
    for vm in vms:
        agg.add("all", vm.get("vcpu"), (vm.get("mem_mb") or 0) * 1024 * 1024, vm.get("power_state") in ("poweredOn", "running"))
    f = agg.fields("all", cores, mem)
    return {
        **about,
        "num_datacenters": len(result.get("datacenters", [])),
        "num_clusters": len(result.get("clusters", [])),
        "num_hosts": len(hosts),
        "num_vms": f["vm_count"],
        "num_vms_on": f["vm_on"],
        "num_datastores": len(dss),
        "total_cores": cores,
        "total_threads": sum(h.get("cpu_threads") or 0 for h in hosts),
        "total_mem_bytes": mem,
        "vcpu_on": f["vcpu_on"],
        "vcpu_all": f["vcpu_all"],
        "vram_on_bytes": f["vram_on_bytes"],
        "vram_all_bytes": f["vram_all_bytes"],
        "cpu_ratio_on": f["cpu_ratio_on"],
        "cpu_ratio_all": f["cpu_ratio_all"],
        "mem_ratio_on": f["mem_ratio_on"],
        "mem_ratio_all": f["mem_ratio_all"],
        "storage_capacity_bytes": sum(d.get("capacity_bytes") or 0 for d in dss),
        "storage_free_bytes": sum(d.get("free_bytes") or 0 for d in dss),
    }


@transaction.atomic
def persist(source: Source, result: dict, about: dict):
    """Remplace l'instantané de la source par la nouvelle collecte (tout ou rien)."""
    now = timezone.now()
    counts = {}
    for key, model in RESULT_MODELS:
        model.objects.filter(source=source).delete()
        rows = result.get(key, [])
        model.objects.bulk_create(
            [model(source=source, collected_at=now, **_clean(model, row)) for row in rows], batch_size=1000
        )
        counts[key] = len(rows)
    InventoryLink.objects.filter(source=source).delete()
    InventoryLink.objects.bulk_create(
        [InventoryLink(source=source, **_clean(InventoryLink, link)) for link in result.get("links", [])], batch_size=2000
    )
    for field, value in summarize(source, result, about).items():
        setattr(source, field, value)
    source.save()
    return counts
