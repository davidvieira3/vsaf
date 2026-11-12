"""HPE OneView : numéros de série ↔ IP iLO, firmware iLO, santé."""
from .base import session


def fetch(connector, log):
    base = connector.url.rstrip("/")
    sess = session(connector)
    version = sess.get(f"{base}/rest/version", timeout=30).json().get("currentVersion", 2000)
    sess.headers["X-API-Version"] = str(version)
    resp = sess.post(f"{base}/rest/login-sessions", json={"userName": connector.username, "password": connector.secret}, timeout=30)
    resp.raise_for_status()
    token = resp.json()["sessionID"]
    sess.headers["Auth"] = token
    records = []
    try:
        url = f"{base}/rest/server-hardware?start=0&count=500"
        while url:
            data = sess.get(url, timeout=60).json()
            for srv in data.get("members", []):
                serial = (srv.get("serialNumber") or "").strip()
                if not serial:
                    continue
                mp = srv.get("mpHostInfo") or {}
                addrs = [a.get("address") for a in mp.get("mpIpAddresses") or [] if a.get("type") != "LinkLocal"]
                ipv4 = next((a for a in addrs if a and ":" not in a), addrs[0] if addrs else "")
                records.append({
                    "serial": serial,
                    "vendor": "HPE",
                    "model": srv.get("model") or srv.get("shortModel") or "",
                    "hostname": srv.get("serverName") or srv.get("name") or "",
                    "bmc_type": srv.get("mpModel") or "iLO",
                    "bmc_ip": ipv4 or "",
                    "bmc_name": mp.get("mpHostName") or "",
                    "bmc_firmware": srv.get("mpFirmwareVersion") or "",
                    "health": srv.get("status") or "",
                    "props": {"rom": srv.get("romVersion"), "power": srv.get("powerState"), "uri": srv.get("uri")},
                })
            nxt = data.get("nextPageUri")
            url = f"{base}{nxt}" if nxt else None
        log.info(f"OneView : {len(records)} serveur(s)")
    finally:
        try:
            sess.delete(f"{base}/rest/login-sessions", timeout=10)
        except Exception:
            pass
    return records
