"""Pure Storage FlashArray : API REST 2.x, authentification par jeton API (utilisateur « readonly »)."""
from collections import defaultdict

import requests

from .base import check, pct, ratio, session


def _version(sess, base):
    versions = check(sess.get(f"{base}/api/api_version", timeout=30)).json().get("version", [])
    v2 = [v for v in versions if v.startswith("2.")]
    if not v2:
        raise RuntimeError("Cette baie n'expose pas l'API REST 2.x (Purity 6.x minimum requis)")
    return max(v2, key=lambda v: tuple(int(x) for x in v.split(".")))


def _items(sess, url, params=None):
    params = dict(params or {}, limit=1000)
    out = []
    while True:
        data = check(sess.get(url, params=params, timeout=180)).json()
        out += data.get("items", [])
        token = data.get("continuation_token")
        if not token:
            return out
        params["continuation_token"] = token


def pure_wwn(serial):
    return ("624a9370" + serial.lower()) if serial else ""


def fetch(system, log):
    sess = session(system)
    base = f"https://{system.host}{':' + str(system.port) if system.port else ''}"
    version = _version(sess, base)
    api = f"{base}/api/{version}"
    resp = check(sess.post(f"{api}/login", headers={"api-token": system.secret}, timeout=30))
    sess.headers["x-auth-token"] = resp.headers["x-auth-token"]
    try:
        arrays = _items(sess, f"{api}/arrays")
        controllers = _items(sess, f"{api}/controllers")
        vols = _items(sess, f"{api}/volumes", {"destroyed": "false"})
        try:
            conns = _items(sess, f"{api}/connections")
        except RuntimeError as exc:
            log.warn(f"Connexions hôtes illisibles : {exc}")
            conns = []
    finally:
        try:
            sess.post(f"{api}/logout", timeout=10)
        except requests.RequestException:
            pass
    arr = arrays[0] if arrays else {}
    log.info(f"FlashArray {arr.get('name')} (API {version}) : {len(vols)} volume(s)")

    hosts = defaultdict(set)
    for c in conns:
        vname = (c.get("volume") or {}).get("name")
        target = (c.get("host_group") or {}).get("name") or (c.get("host") or {}).get("name")
        if vname and target:
            hosts[vname].add(target)

    volumes = []
    for v in vols:
        sp = v.get("space") or {}
        size = v.get("provisioned") or sp.get("total_provisioned") or 0
        used = sp.get("total_physical") or sp.get("unique") or 0
        volumes.append({
            "name": v.get("name", ""),
            "kind": v.get("subtype") or "LUN",
            "pool": (v.get("volume_group") or {}).get("name") or "",
            "svm": (v.get("pod") or {}).get("name") or "",
            "state": "actif",
            "size_bytes": size,
            "used_bytes": used,
            "used_pct": pct(sp.get("virtual") or 0, size),
            "thin": True,
            "data_reduction": ratio(sp.get("data_reduction")),
            "wwn": pure_wwn(v.get("serial", "")),
            "hosts": ", ".join(sorted(hosts.get(v.get("name"), []))),
            "props": {"serial": v.get("serial"), "written_bytes": sp.get("virtual"), "snapshots_bytes": sp.get("snapshots"),
                      "qos": v.get("qos")},
        })

    sp = arr.get("space") or {}
    cap = arr.get("capacity") or 0
    used = sp.get("total_physical") or 0
    summary = {
        "vendor": "Pure Storage",
        "model": ", ".join(sorted({c.get("model", "") for c in controllers if c.get("model")})),
        "serial": arr.get("id", ""),
        "version": f"{arr.get('os', 'Purity')} {arr.get('version', '')}".strip(),
        "num_nodes": sum(1 for c in controllers if (c.get("mode") or "") in ("primary", "secondary")) or len(controllers),
        "capacity_bytes": cap,
        "used_bytes": used,
        "free_bytes": max(cap - used, 0),
        "used_pct": pct(used, cap),
        "provisioned_bytes": sp.get("total_provisioned") or sum(v["size_bytes"] for v in volumes),
        "data_reduction": ratio(sp.get("data_reduction")),
        "info": {"name": arr.get("name"), "api": version,
                 "controllers": [{"name": c.get("name"), "model": c.get("model"), "status": c.get("status"), "mode": c.get("mode")} for c in controllers],
                 "space": sp},
    }
    return summary, [], volumes
