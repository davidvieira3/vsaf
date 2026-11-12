"""HPE 3PAR, Primera, Alletra 9000 et Alletra MP Block : API WSAPI (rôle « browse » suffisant)."""
from collections import defaultdict

import requests

from .base import MIB, check, pct, session

RAID = {1: "RAID 0", 2: "RAID 1", 3: "RAID 5", 4: "RAID 6"}
PROV = {1: "full", 2: "thin (TPVV)", 3: "snapshot", 4: "peer", 5: "inconnu", 6: "thin dédupliqué (TDVV)", 7: "dds"}


def _usage(cpg, key):
    u = cpg.get(key) or {}
    return u.get("totalMiB", 0), u.get("usedMiB", 0)


def fetch(system, log):
    sess = session(system)
    ports = [system.port] if system.port else [443, 8080]
    base, key = None, None
    last_exc = None
    for port in ports:
        candidate = f"https://{system.host}:{port}/api/v1"
        try:
            resp = sess.post(f"{candidate}/credentials", json={"user": system.username, "password": system.secret}, timeout=30)
            check(resp)
            base, key = candidate, resp.json()["key"]
            break
        except (requests.RequestException, RuntimeError) as exc:
            last_exc = exc
    if key is None:
        raise RuntimeError(f"Connexion WSAPI impossible : {last_exc}")
    sess.headers["X-HP3PAR-WSAPI-SessionKey"] = key
    try:
        sysinfo = check(sess.get(f"{base}/system", timeout=60)).json()
        cpgs = check(sess.get(f"{base}/cpgs", timeout=120)).json().get("members", [])
        vols = check(sess.get(f"{base}/volumes", timeout=300)).json().get("members", [])
        try:
            vluns = check(sess.get(f"{base}/vluns", timeout=300)).json().get("members", [])
        except RuntimeError as exc:
            log.warn(f"VLUN illisibles : {exc}")
            vluns = []
    finally:
        try:
            sess.delete(f"{base}/credentials/{key}", timeout=10)
        except requests.RequestException:
            pass
    log.info(f"{sysinfo.get('model')} {sysinfo.get('name')} : {len(cpgs)} CPG, {len(vols)} volumes")

    hosts = defaultdict(set)
    for v in vluns:
        if v.get("volumeName") and v.get("hostname"):
            hosts[v["volumeName"]].add(v["hostname"])

    vol_count = defaultdict(int)
    volumes = []
    for v in vols:
        if v.get("copyType") not in (None, 1):  # 1 = volume de base ; on ignore snapshots et copies
            continue
        size = (v.get("sizeMiB") or 0) * MIB
        used = (v.get("totalUsedMiB") or (v.get("usrSpace") or {}).get("usedMiB") or 0) * MIB
        vol_count[v.get("userCPG", "")] += 1
        volumes.append({
            "name": v.get("name", ""),
            "kind": "LUN",
            "pool": v.get("userCPG", ""),
            "state": {1: "normal", 2: "dégradé", 3: "en échec"}.get(v.get("state"), str(v.get("state", ""))),
            "size_bytes": size,
            "used_bytes": used,
            "used_pct": pct(used, size),
            "thin": v.get("provisioningType") in (2, 6),
            "wwn": v.get("wwn", ""),
            "hosts": ", ".join(sorted(hosts.get(v.get("name"), []))),
            "props": {"provisioning": PROV.get(v.get("provisioningType")), "snap_cpg": v.get("snapCPG"),
                      "compression": v.get("compressionState"), "dedup": v.get("deduplicationState"), "id": v.get("id")},
        })

    pools = []
    for c in cpgs:
        total = sum(_usage(c, k)[0] for k in ("UsrUsage", "SAUsage", "SDUsage")) * MIB
        used = sum(_usage(c, k)[1] for k in ("UsrUsage", "SAUsage", "SDUsage")) * MIB
        if c.get("totalSpaceMiB"):  # Primera / Alletra
            total = c["totalSpaceMiB"] * MIB
            used = (c.get("totalSpaceMiB", 0) - c.get("freeSpaceMiB", 0)) * MIB if "freeSpaceMiB" in c else used
        raid = (((c.get("SDGrowth") or {}).get("LDLayout") or {}).get("RAIDType"))
        pools.append({
            "name": c.get("name", ""),
            "kind": "CPG",
            "raid": RAID.get(raid, ""),
            "state": {1: "normal", 2: "dégradé", 3: "en échec"}.get(c.get("state"), ""),
            "capacity_bytes": total,
            "used_bytes": used,
            "free_bytes": max(total - used, 0),
            "used_pct": pct(used, total),
            "num_volumes": vol_count.get(c.get("name"), 0),
            "props": {"tpvv": c.get("numTPVVs"), "tdvv": c.get("numTDVVs"), "fpvv": c.get("numFPVVs")},
        })

    cap = (sysinfo.get("totalCapacityMiB") or 0) * MIB
    alloc = (sysinfo.get("allocatedCapacityMiB") or 0) * MIB
    free = (sysinfo.get("freeCapacityMiB") or 0) * MIB
    summary = {
        "vendor": "HPE",
        "model": sysinfo.get("model", ""),
        "serial": str(sysinfo.get("serialNumber", "")),
        "version": sysinfo.get("systemVersion", ""),
        "num_nodes": sysinfo.get("totalNodes") or 0,
        "capacity_bytes": cap,
        "used_bytes": alloc,
        "free_bytes": free,
        "used_pct": pct(alloc, cap),
        "provisioned_bytes": sum(v["size_bytes"] for v in volumes),
        "data_reduction": None,
        "info": {"name": sysinfo.get("name"), "online_nodes": sysinfo.get("onlineNodes"), "ip": sysinfo.get("IPv4Addr"),
                 "location": sysinfo.get("location"), "owner": sysinfo.get("owner")},
    }
    return summary, pools, volumes
