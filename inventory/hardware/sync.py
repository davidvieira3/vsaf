from django.db import transaction
from django.db.models import Q

from inventory.models import HardwareRecord, Host

from . import FETCHERS


def sync_connector(connector, log):
    records = FETCHERS[connector.kind](connector, log)
    limits = {f.name: f.max_length for f in HardwareRecord._meta.fields if getattr(f, "max_length", None)}

    def clean(r):
        return {k: (str(v or "")[: limits[k]] if k in limits else v) for k, v in r.items()}

    with transaction.atomic():
        HardwareRecord.objects.filter(connector=connector).delete()
        HardwareRecord.objects.bulk_create([HardwareRecord(connector=connector, **clean(r)) for r in records], batch_size=1000)
    matched = apply_hardware()
    log.info(f"{matched} hôte(s) rapproché(s) par numéro de série")
    return {"records": len(records), "matched_hosts": matched}


def apply_hardware(host_qs=None):
    """Recopie les infos BMC sur les hôtes dont le numéro de série correspond."""
    index = {}
    for rec in HardwareRecord.objects.select_related("connector").filter(connector__enabled=True):
        for key in (rec.serial, rec.alt_serial):
            if key:
                index[key.upper()] = rec
    hosts = host_qs if host_qs is not None else Host.objects.exclude(Q(serial="") | Q(serial__isnull=True))
    to_update = []
    for h in hosts:
        rec = index.get((h.serial or "").upper())
        if rec is None:
            continue
        h.bmc_type = rec.bmc_type[:64]
        h.bmc_ip = rec.bmc_ip[:64]
        h.bmc_name = rec.bmc_name[:255]
        h.bmc_firmware = rec.bmc_firmware[:64]
        h.bmc_health = rec.health[:32]
        h.bmc_source = rec.connector.name[:64]
        to_update.append(h)
    Host.objects.bulk_update(to_update, ["bmc_type", "bmc_ip", "bmc_name", "bmc_firmware", "bmc_health", "bmc_source"], batch_size=1000)
    return len(to_update)
