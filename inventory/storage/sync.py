from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from inventory.models import Datastore, StoragePool, StorageSystem, StorageVolume

from . import fetcher_for
from .base import normalize_wwn

SUMMARY_FIELDS = ["vendor", "model", "serial", "version", "num_nodes", "capacity_bytes", "used_bytes", "free_bytes",
                  "used_pct", "provisioned_bytes", "data_reduction", "info"]


def _clean(model, row):
    meta = {f.name: getattr(f, "max_length", None) for f in model._meta.concrete_fields}
    out = {}
    for k, v in row.items():
        if k in meta and k not in ("id", "system"):
            out[k] = str(v or "")[: meta[k]] if meta[k] else v
    return out


def collect_storage(system: StorageSystem, log):
    summary, pools, volumes = fetcher_for(system.kind)(system, log)
    with transaction.atomic():
        StoragePool.objects.filter(system=system).delete()
        StorageVolume.objects.filter(system=system).delete()
        StoragePool.objects.bulk_create([StoragePool(system=system, **_clean(StoragePool, p)) for p in pools], batch_size=1000)
        StorageVolume.objects.bulk_create([StorageVolume(system=system, **_clean(StorageVolume, v)) for v in volumes], batch_size=1000)
        for f in SUMMARY_FIELDS:
            value = summary.get(f)
            if f in ("vendor", "model", "serial", "version"):
                value = str(value or "")[:128]
            setattr(system, f, value if value is not None or f in ("used_pct", "data_reduction") else 0)
        system.num_pools, system.num_volumes = len(pools), len(volumes)
        system.last_success_at = timezone.now()
        system.save()
    matched = map_datastores()
    log.info(f"{matched} datastore(s) rattaché(s) à un volume de baie")
    return {"pools": len(pools), "volumes": len(volumes), "datastores_rattaches": matched}


def map_datastores():
    """Rattache les datastores (VMFS par WWN/NAA des extents, NFS par chemin d'export) aux volumes des baies."""
    by_wwn, by_path = {}, {}
    for v in StorageVolume.objects.select_related("system").only("id", "name", "wwn", "nas_path", "svm", "system__name"):
        if v.wwn:
            by_wwn[normalize_wwn(v.wwn)] = v
        if v.nas_path and v.nas_path != "/":
            by_path[v.nas_path.rstrip("/")] = v
    ds_updates, vol_ds = [], defaultdict(set)
    for ds in Datastore.objects.select_related("source").only("id", "name", "extents", "remote", "array_name", "array_volume", "source__name"):
        found = []
        for ext in filter(None, (e.strip() for e in ds.extents.split(","))):
            vol = by_wwn.get(normalize_wwn(ext.split(":")[0]))
            if vol:
                found.append(vol)
        if not found and ds.remote and ":" in ds.remote and not ds.remote.startswith("ceph"):
            path = ds.remote.split(":", 1)[1].split(" ")[0].rstrip("/")
            vol = by_path.get(path)
            if vol:
                found.append(vol)
        array_name = ", ".join(sorted({v.system.name for v in found}))[:255]
        array_volume = ", ".join(f"{v.svm + ':' if v.svm else ''}{v.name}" for v in found)[:500]
        for v in found:
            vol_ds[v.pk].add(f"{ds.name} ({ds.source.name})")
        if (array_name, array_volume) != (ds.array_name, ds.array_volume):
            ds.array_name, ds.array_volume = array_name, array_volume
            ds_updates.append(ds)
    Datastore.objects.bulk_update(ds_updates, ["array_name", "array_volume"], batch_size=1000)
    vol_updates = []
    for v in StorageVolume.objects.only("id", "datastores"):
        text = ", ".join(sorted(vol_ds.get(v.pk, [])))
        if text != v.datastores:
            v.datastores = text
            vol_updates.append(v)
    StorageVolume.objects.bulk_update(vol_updates, ["datastores"], batch_size=1000)
    return sum(1 for d in Datastore.objects.exclude(array_name="").only("id"))


def test_storage(system, log):
    summary, pools, volumes = fetcher_for(system.kind)(system, log)
    return f"{summary.get('vendor')} {summary.get('model')} n° {summary.get('serial')} — {summary.get('version')} — {len(pools)} pool(s), {len(volumes)} volume(s)"
