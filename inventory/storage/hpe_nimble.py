"""HPE Alletra 5000/6000 et Nimble : API REST v1 (port 5392, utilisateur rôle « guest » ou « operator »)."""
from collections import defaultdict

import requests

from .base import MIB, check, pct, ratio, session


def _all(sess, url, log):
    rows, start, page = [], 0, 1000
    while True:
        data = check(sess.get(url, params={"startRow": start, "endRow": start + page}, timeout=120)).json()
        chunk = data.get("data", [])
        rows += chunk
        total = data.get("totalRows", len(rows))
        start += page
        if not chunk or len(rows) >= total:
            return rows


def fetch(system, log):
    sess = session(system)
    base = f"https://{system.host}:{system.port or 5392}/v1"
    resp = check(sess.post(f"{base}/tokens", json={"data": {"username": system.username, "password": system.secret}}, timeout=30))
    token = resp.json()["data"]
    sess.headers["X-Auth-Token"] = token["session_token"]
    try:
        groups = check(sess.get(f"{base}/groups/detail", timeout=60)).json().get("data", [])
        arrays = _all(sess, f"{base}/arrays/detail", log)
        pools_raw = _all(sess, f"{base}/pools/detail", log)
        vols = _all(sess, f"{base}/volumes/detail", log)
        try:
            acrs = _all(sess, f"{base}/access_control_records/detail", log)
        except RuntimeError as exc:
            log.warn(f"Droits d'accès illisibles : {exc}")
            acrs = []
    finally:
        try:
            sess.delete(f"{base}/tokens/{token.get('id')}", timeout=10)
        except requests.RequestException:
            pass
    group = groups[0] if groups else {}
    log.info(f"Groupe {group.get('name')} : {len(arrays)} baie(s), {len(pools_raw)} pool(s), {len(vols)} volume(s)")

    hosts = defaultdict(set)
    for a in acrs:
        if a.get("vol_name") and a.get("initiator_group_name"):
            hosts[a["vol_name"]].add(a["initiator_group_name"])

    volumes = []
    for v in vols:
        size = (v.get("size") or 0) * MIB
        used = v.get("total_usage_bytes") or v.get("vol_usage_compressed_bytes") or 0
        volumes.append({
            "name": v.get("name", ""),
            "kind": "LUN",
            "pool": v.get("pool_name", ""),
            "svm": v.get("folder_name") or "",
            "state": "en ligne" if v.get("online") else "hors ligne",
            "size_bytes": size,
            "used_bytes": used,
            "used_pct": pct(used, size),
            "thin": v.get("thinly_provisioned"),
            "data_reduction": ratio(v.get("compression_ratio") or v.get("savings_ratio")),
            "wwn": v.get("serial_number", ""),
            "hosts": ", ".join(sorted(hosts.get(v.get("name"), []))),
            "props": {"perf_policy": v.get("perfpolicy_name"), "target": v.get("target_name"),
                      "protection": v.get("volcoll_name"), "dedupe": v.get("dedupe_enabled")},
        })

    pools = []
    for p in pools_raw:
        cap = p.get("capacity") or 0
        used = p.get("usage") or 0
        pools.append({
            "name": p.get("name", ""),
            "kind": "Pool",
            "capacity_bytes": cap,
            "used_bytes": used,
            "free_bytes": p.get("free_space") or max(cap - used, 0),
            "used_pct": pct(used, cap),
            "data_reduction": ratio(p.get("savings_ratio") or p.get("compression_ratio")),
            "num_volumes": p.get("vol_count") or len(p.get("vol_list") or []),
            "props": {"arrays": [a.get("array_name") for a in (p.get("array_list") or [])], "dedupe": p.get("dedupe_capable")},
        })

    cap = sum(a.get("usable_capacity_bytes") or 0 for a in arrays)
    used = sum(a.get("usage") or 0 for a in arrays)
    summary = {
        "vendor": "HPE",
        "model": ", ".join(sorted({a.get("extended_model") or a.get("model") or "" for a in arrays})),
        "serial": ", ".join(a.get("serial", "") for a in arrays),
        "version": group.get("version_current") or (arrays[0].get("version") if arrays else ""),
        "num_nodes": len(arrays),
        "capacity_bytes": cap,
        "used_bytes": used,
        "free_bytes": max(cap - used, 0),
        "used_pct": pct(used, cap),
        "provisioned_bytes": sum(v["size_bytes"] for v in volumes),
        "data_reduction": ratio(group.get("savings_ratio") or group.get("compression_ratio")),
        "info": {"group": group.get("name"), "arrays": [a.get("name") for a in arrays]},
    }
    return summary, pools, volumes
