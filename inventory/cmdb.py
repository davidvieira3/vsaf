"""Intégration ServiceNow CMDB (lecture seule, API Table) : propriétaire, groupe de support... des VM."""
import re
from collections import defaultdict

import requests
from django.db import transaction

from core.tls import requests_verify

from .models import CmdbRecord, VirtualMachine

TYPED_COLUMNS = {"owner": "cmdb_owner", "managed_by": "cmdb_managed_by", "support_group": "cmdb_support_group",
                 "environment": "cmdb_environment", "status": "cmdb_status", "application": "cmdb_application"}
PAGE = 2000
_NORM = re.compile(r"[^0-9a-z]")


def norm(value):
    """Clé de comparaison : minuscules, sans tirets ni espaces (les UUID sont écrits de plusieurs façons)."""
    return _NORM.sub("", str(value or "").lower())


def _session(conn):
    sess = requests.Session()
    sess.verify = requests_verify(conn.verify_ssl)
    sess.headers["Accept"] = "application/json"
    base = conn.instance_url.rstrip("/")
    if conn.auth_mode == "oauth":
        resp = sess.post(f"{base}/oauth_token.do", data={
            "grant_type": "client_credentials", "client_id": conn.client_id, "client_secret": conn.client_secret}, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"OAuth refusé ({resp.status_code}) : {resp.text[:200]}")
        sess.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    else:
        sess.auth = (conn.username, conn.secret)
    return sess, base


def _fields(conn):
    match_fields = set()
    for snow, _ in conn.rules():
        match_fields.update(snow.split("+"))
    return sorted({"sys_id", "name"} | match_fields | {f for _, f in conn.mapping()} | set(conn.extras()))


def fetch_records(conn, log, limit=None):
    sess, base = _session(conn)
    fields = _fields(conn)
    url = f"{base}/api/now/table/{conn.table}"
    offset, out = 0, []
    while True:
        params = {
            "sysparm_fields": ",".join(fields),
            "sysparm_display_value": "all",  # valeur brute + libellé (utile pour les références : owned_by...)
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": min(PAGE, limit or PAGE),
            "sysparm_offset": offset,
            "sysparm_query": (conn.query + "^" if conn.query else "") + "ORDERBYsys_id",
        }
        resp = sess.get(url, params=params, timeout=180)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code} sur {conn.table} : {resp.text[:300]}")
        rows = resp.json().get("result", [])
        out += rows
        offset += len(rows)
        if len(rows) < params["sysparm_limit"] or (limit and len(out) >= limit):
            break
    log.info(f"ServiceNow : {len(out)} enregistrement(s) lus dans {conn.table}")
    return out


def _val(row, field, display=True):
    v = row.get(field)
    if isinstance(v, dict):
        return (v.get("display_value") if display else v.get("value")) or ""
    return v or ""


def sync_cmdb(conn, log):
    rows = fetch_records(conn, log)
    records = []
    for r in rows:
        keys = {}
        for snow, _ in conn.rules():
            keys[snow] = "|".join(norm(_val(r, f, display=False)) for f in snow.split("+"))
        values = {col: _val(r, f) for col, f in conn.mapping()}
        values["_extra"] = {f: _val(r, f) for f in conn.extras()}
        records.append(CmdbRecord(connector=conn, sys_id=_val(r, "sys_id", display=False)[:32], name=_val(r, "name")[:255],
                                  keys=keys, values=values))
    with transaction.atomic():
        CmdbRecord.objects.filter(connector=conn).delete()
        CmdbRecord.objects.bulk_create(records, batch_size=2000)
    matched = apply_cmdb()
    log.info(f"{matched} VM rapprochée(s) d'un CI ServiceNow")
    return {"records": len(records), "vm_rapprochees": matched}


def _vm_key(vm, app_key):
    parts = []
    for k in app_key.split("+"):
        if k == "uuid":
            parts.append(vm.uuid)
        elif k == "bios_uuid":
            parts.append(vm.bios_uuid)
        elif k == "moid":
            parts.append(vm.moid)
        elif k == "vcenter_uuid":
            parts.append(vm.source.instance_uuid)
        elif k == "name":
            parts.append(vm.name)
        elif k == "vm_id":
            parts.append(vm.vm_id)
        elif k == "hostname":
            parts.append(vm.guest_hostname.split(".")[0])
        else:
            return ""
    keys = [norm(p) for p in parts]
    return "" if not all(keys) else "|".join(keys)


def apply_cmdb(vm_qs=None):
    """Recopie les valeurs CMDB sur les VM. Appelée après chaque collecte et chaque synchro ServiceNow."""
    from .models import CmdbConnector

    connectors = list(CmdbConnector.objects.filter(enabled=True))
    indexes = []  # [(connecteur, champ_snow, clé_app, {valeur: record | None si ambigu})]
    for conn in connectors:
        recs = list(CmdbRecord.objects.filter(connector=conn))
        for snow, app_key in conn.rules():
            idx = {}
            for rec in recs:
                k = rec.keys.get(snow)
                if not k or k.strip("|") == "":
                    continue
                idx[k] = None if k in idx else rec  # doublon → ambigu, ignoré
            indexes.append((conn, snow, app_key, idx))
    vms = vm_qs if vm_qs is not None else VirtualMachine.objects.select_related("source")
    updates, matched = [], 0
    fields = list(TYPED_COLUMNS.values()) + ["cmdb_sys_id", "cmdb_url", "cmdb_match", "cmdb_extra"]
    for vm in vms.iterator(chunk_size=2000):
        rec, how = None, ""
        for conn, snow, app_key, idx in indexes:
            key = _vm_key(vm, app_key)
            if key and idx.get(key):
                rec, how = idx[key], f"{snow}={app_key}"
                break
        new = dict.fromkeys(fields, "")
        new["cmdb_extra"] = {}
        if rec:
            matched += 1
            for col, attr in TYPED_COLUMNS.items():
                new[attr] = str(rec.values.get(col, ""))[:255]
            new["cmdb_environment"] = new["cmdb_environment"][:128]
            new["cmdb_status"] = new["cmdb_status"][:64]
            new["cmdb_sys_id"] = rec.sys_id
            new["cmdb_url"] = f"{rec.connector.instance_url.rstrip('/')}/nav_to.do?uri={rec.connector.table}.do?sys_id={rec.sys_id}"[:500]
            new["cmdb_match"] = how[:64]
            new["cmdb_extra"] = rec.values.get("_extra", {})
        if any(getattr(vm, f) != v for f, v in new.items()):
            for f, v in new.items():
                setattr(vm, f, v)
            updates.append(vm)
        if len(updates) >= 2000:
            VirtualMachine.objects.bulk_update(updates, fields)
            updates = []
    if updates:
        VirtualMachine.objects.bulk_update(updates, fields)
    return matched


def test_cmdb(conn, log):
    rows = fetch_records(conn, log, limit=5)
    if not rows:
        return f"Connexion réussie, mais aucun enregistrement dans {conn.table} avec ce filtre."
    sample = rows[0]
    missing = [f for f in _fields(conn) if f not in sample]
    msg = f"Connexion réussie à {conn.table} ; exemple : {_val(sample, 'name')}"
    if missing:
        msg += f". Champs inconnus de la table (à corriger) : {', '.join(missing)}"
    return msg
