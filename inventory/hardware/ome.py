"""Dell OpenManage Enterprise : numéros de série (service tag) ↔ IP iDRAC, modèle, santé."""
from .base import HEALTH, session


def fetch(connector, log):
    base = connector.url.rstrip("/")
    sess = session(connector)
    resp = sess.post(f"{base}/api/SessionService/Sessions",
                     json={"UserName": connector.username, "Password": connector.secret, "SessionType": "API"}, timeout=30)
    resp.raise_for_status()
    sess.headers["X-Auth-Token"] = resp.headers["X-Auth-Token"]
    session_id = resp.json().get("Id")
    records = []
    try:
        url = f"{base}/api/DeviceService/Devices?$top=500"
        while url:
            data = sess.get(url, timeout=60).json()
            for dev in data.get("value", []):
                tag = (dev.get("DeviceServiceTag") or "").strip()
                if not tag:
                    continue
                mgmt = (dev.get("DeviceManagement") or [{}])
                mgmt0 = mgmt[0] if mgmt else {}
                profiles = mgmt0.get("ManagementProfile") or [{}]
                prof = profiles[0] if profiles else {}
                records.append({
                    "serial": tag,
                    "vendor": "Dell",
                    "model": dev.get("Model") or "",
                    "hostname": dev.get("DeviceName") or "",
                    "bmc_type": prof.get("AgentName") or "iDRAC",
                    "bmc_ip": mgmt0.get("NetworkAddress") or "",
                    "bmc_name": mgmt0.get("DnsName") or "",
                    "bmc_firmware": prof.get("Version") or "",
                    "health": HEALTH.get(dev.get("Status"), str(dev.get("Status") or "")),
                    "props": {"ome_id": dev.get("Id"), "chassis": dev.get("ChassisServiceTag"), "power": dev.get("PowerState"),
                              "management_url": prof.get("ManagementURL")},
                })
            nxt = data.get("@odata.nextLink")
            url = f"{base}{nxt}" if nxt and nxt.startswith("/") else nxt
        log.info(f"OME : {len(records)} équipement(s)")
    finally:
        if session_id:
            try:
                sess.delete(f"{base}/api/SessionService/Sessions('{session_id}')", timeout=10)
            except Exception:
                pass
    return records
