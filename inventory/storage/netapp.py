"""NetApp ONTAP 9.6+ : API REST (rôle « readonly » suffisant)."""
from collections import defaultdict

from .base import check, pct, ratio, session


def _records(sess, base, path, fields, extra=None):
    params = {"fields": fields, "max_records": 1000, **(extra or {})}
    url, out = f"{base}{path}", []
    while url:
        data = check(sess.get(url, params=params, timeout=180)).json()
        out += data.get("records", [])
        nxt = ((data.get("_links") or {}).get("next") or {}).get("href")
        url, params = (f"https://{base.split('/')[2]}{nxt}", None) if nxt else (None, None)
    return out


def lun_wwn(serial):
    """Un LUN ONTAP apparaît côté ESXi en naa.600a0980 + numéro de série ASCII en hexadécimal."""
    return ("600a0980" + serial.encode().hex()) if serial else ""


def fetch(system, log):
    sess = session(system)
    sess.auth = (system.username, system.secret)
    base = f"https://{system.host}{':' + str(system.port) if system.port else ''}/api"
    cluster = check(sess.get(f"{base}/cluster", params={"fields": "name,uuid,version"}, timeout=30)).json()
    nodes = _records(sess, base, "/cluster/nodes", "name,model,serial_number,state,version")
    aggrs = _records(sess, base, "/storage/aggregates",
                     "name,node.name,state,block_storage.primary.raid_type,space.block_storage.size,"
                     "space.block_storage.used,space.block_storage.available,space.efficiency.ratio,volume_count")
    vols = _records(sess, base, "/storage/volumes",
                    "name,svm.name,aggregates.name,state,type,style,space.size,space.used,space.available,nas.path,guarantee.type",
                    {"is_constituent": "false"})
    try:
        luns = _records(sess, base, "/storage/luns",
                        "name,svm.name,location.volume.name,space.size,space.used,serial_number,os_type,status.state,lun_maps.igroup.name")
    except RuntimeError as exc:
        log.warn(f"LUN illisibles : {exc}")
        luns = []
    log.info(f"Cluster {cluster.get('name')} : {len(nodes)} nœud(s), {len(aggrs)} agrégat(s), {len(vols)} volume(s), {len(luns)} LUN")

    vol_per_aggr = defaultdict(int)
    volumes = []
    for v in vols:
        if v.get("type") == "dp":
            continue  # destinations SnapMirror : comptées côté source
        sp = v.get("space") or {}
        size, used = sp.get("size") or 0, sp.get("used") or 0
        aggr_names = [a.get("name") for a in v.get("aggregates") or []]
        for a in aggr_names:
            vol_per_aggr[a] += 1
        volumes.append({
            "name": v.get("name", ""),
            "kind": "Volume NAS" if (v.get("nas") or {}).get("path") else "Volume",
            "pool": ", ".join(filter(None, aggr_names)),
            "svm": (v.get("svm") or {}).get("name", ""),
            "state": v.get("state", ""),
            "size_bytes": size,
            "used_bytes": used,
            "used_pct": pct(used, size),
            "thin": (v.get("guarantee") or {}).get("type") == "none",
            "nas_path": (v.get("nas") or {}).get("path", "") or "",
            "props": {"style": v.get("style"), "type": v.get("type"), "available": sp.get("available")},
        })
    for lun in luns:
        sp = lun.get("space") or {}
        size, used = sp.get("size") or 0, sp.get("used") or 0
        volumes.append({
            "name": lun.get("name", ""),
            "kind": "LUN",
            "pool": ((lun.get("location") or {}).get("volume") or {}).get("name", ""),
            "svm": (lun.get("svm") or {}).get("name", ""),
            "state": (lun.get("status") or {}).get("state", ""),
            "size_bytes": size,
            "used_bytes": used,
            "used_pct": pct(used, size),
            "wwn": lun_wwn(lun.get("serial_number", "")),
            "hosts": ", ".join(sorted({(m.get("igroup") or {}).get("name", "") for m in lun.get("lun_maps") or []} - {""})),
            "props": {"serial": lun.get("serial_number"), "os_type": lun.get("os_type")},
        })

    pools = []
    for a in aggrs:
        bs = (a.get("space") or {}).get("block_storage") or {}
        cap, used = bs.get("size") or 0, bs.get("used") or 0
        pools.append({
            "name": a.get("name", ""),
            "kind": "Agrégat",
            "node": (a.get("node") or {}).get("name", ""),
            "raid": (((a.get("block_storage") or {}).get("primary") or {}).get("raid_type") or ""),
            "state": a.get("state", ""),
            "capacity_bytes": cap,
            "used_bytes": used,
            "free_bytes": bs.get("available") or max(cap - used, 0),
            "used_pct": pct(used, cap),
            "data_reduction": ratio(((a.get("space") or {}).get("efficiency") or {}).get("ratio")),
            "num_volumes": a.get("volume_count") or vol_per_aggr.get(a.get("name"), 0),
        })

    cap = sum(p["capacity_bytes"] for p in pools)
    used = sum(p["used_bytes"] for p in pools)
    summary = {
        "vendor": "NetApp",
        "model": ", ".join(sorted({n.get("model", "") for n in nodes})),
        "serial": ", ".join(n.get("serial_number", "") for n in nodes),
        "version": ((cluster.get("version") or {}).get("full") or ""),
        "num_nodes": len(nodes),
        "capacity_bytes": cap,
        "used_bytes": used,
        "free_bytes": max(cap - used, 0),
        "used_pct": pct(used, cap),
        "provisioned_bytes": sum(v["size_bytes"] for v in volumes if v["kind"] != "LUN"),
        "data_reduction": None,
        "info": {"cluster": cluster.get("name"), "uuid": cluster.get("uuid"),
                 "nodes": [{"name": n.get("name"), "model": n.get("model"), "state": n.get("state")} for n in nodes]},
    }
    return summary, pools, volumes
