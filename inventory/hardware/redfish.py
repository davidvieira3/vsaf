"""Redfish direct : interroge une liste de BMC (iDRAC, iLO, XClarity, Supermicro, iRMC...)."""
from concurrent.futures import ThreadPoolExecutor

from .base import expand_hosts, session


def _get(sess, url):
    resp = sess.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _one(connector, address):
    sess = session(connector)
    sess.auth = (connector.username, connector.secret)
    base = f"https://{address}"
    systems = _get(sess, f"{base}/redfish/v1/Systems")
    system = _get(sess, base + systems["Members"][0]["@odata.id"])
    managers = _get(sess, f"{base}/redfish/v1/Managers")
    manager = _get(sess, base + managers["Members"][0]["@odata.id"])
    vendor = system.get("Manufacturer") or ""
    sku = (system.get("SKU") or "").strip()  # Dell : SKU = service tag
    serial = (system.get("SerialNumber") or "").strip()
    primary, alt = (sku, serial) if "dell" in vendor.lower() and sku else (serial, sku)
    return {
        "serial": primary,
        "alt_serial": alt,
        "vendor": vendor,
        "model": system.get("Model") or "",
        "hostname": system.get("HostName") or "",
        "bmc_type": manager.get("Model") or manager.get("Name") or "BMC",
        "bmc_ip": address,
        "bmc_name": (manager.get("HostName") or ""),
        "bmc_firmware": manager.get("FirmwareVersion") or "",
        "health": (system.get("Status") or {}).get("HealthRollup") or (system.get("Status") or {}).get("Health") or "",
        "props": {"bios": system.get("BiosVersion"), "power": system.get("PowerState")},
    }


def fetch(connector, log):
    hosts = expand_hosts(connector.bmc_hosts)
    log.info(f"Redfish : {len(hosts)} BMC à interroger")
    records = []

    def worker(addr):
        try:
            return _one(connector, addr)
        except Exception as exc:
            log.warn(f"{addr} : {exc.__class__.__name__} {exc}")
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        for rec in pool.map(worker, hosts):
            if rec and rec["serial"]:
                records.append(rec)
    log.info(f"Redfish : {len(records)} réponse(s) exploitable(s)")
    return records
