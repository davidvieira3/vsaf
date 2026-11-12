"""Enrichissement facultatif des nœuds Proxmox par SSH, exécuté par l'application (pas par l'utilisateur).

L'API Proxmox n'expose ni le numéro de série, ni l'adresse du BMC, ni les voisins LLDP.
Prérequis sur les nœuds : dmidecode, ipmitool, lldpd (apt install lldpd ipmitool).
Compte conseillé : utilisateur dédié avec sudo NOPASSWD limité à dmidecode, ipmitool et lldpctl.
"""
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor

import paramiko

SCRIPT = r"""
P=""; [ "$(id -u)" -ne 0 ] && P="sudo -n"
echo "##DMI"
for k in system-manufacturer system-product-name system-serial-number bios-version bios-release-date; do
  v=$($P dmidecode -s $k 2>/dev/null | grep -v '^#' | head -1); echo "$k=$v"
done
echo "##IPMI"; $P ipmitool lan print 1 2>/dev/null
echo "##LLDP"; $P lldpctl -f json 2>/dev/null
echo "##NIC"
for d in /sys/class/net/*; do n=$(basename $d); [ -e $d/device ] || continue
  echo "$n $(cat $d/address 2>/dev/null) $(cat $d/speed 2>/dev/null)"; done
echo "##END"
"""

BMC_BY_VENDOR = {"dell": "iDRAC", "hpe": "iLO", "hewlett": "iLO", "lenovo": "XClarity (XCC)", "supermicro": "Supermicro BMC", "fujitsu": "iRMC", "cisco": "CIMC"}


def _sections(out):
    sections, current = {}, None
    for line in out.splitlines():
        if line.startswith("##"):
            current = line[2:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def _first(value):
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def parse_lldp(text):
    result = {}
    try:
        data = json.loads(text)
    except Exception:
        return result
    raw = (data.get("lldp") or {}).get("interface") or []
    items = list(raw.items()) if isinstance(raw, dict) else [kv for d in raw for kv in d.items()]
    for ifname, info in items:
        chassis = info.get("chassis") or {}
        if "id" in chassis and isinstance(chassis.get("id"), dict):
            sw_name, ch = _first(chassis["id"].get("value")), chassis
        else:
            sw_name, ch = next(iter(chassis.items()), ("", {}))
        port = info.get("port") or {}
        vlan = info.get("vlan")
        vlan = vlan[0] if isinstance(vlan, list) and vlan else (vlan or {})
        result[ifname] = {
            "protocol": str(info.get("via") or "LLDP")[:8],
            "switch_name": sw_name,
            "switch_ip": _first(ch.get("mgmt-ip")),
            "switch_platform": _first(ch.get("descr"))[:255],
            "switch_port": _first((port.get("id") or {}).get("value")),
            "switch_port_descr": _first(port.get("descr")),
            "switch_vlan": str(vlan.get("vlan-id", "")) if isinstance(vlan, dict) else "",
            "raw": info,
        }
    return result


def parse_output(out):
    sec = _sections(out)
    dmi = dict(line.split("=", 1) for line in sec.get("DMI", "").splitlines() if "=" in line)
    vendor = dmi.get("system-manufacturer", "").strip()
    ipmi = sec.get("IPMI", "")
    m = re.search(r"^IP Address\s+:\s*(\S+)", ipmi, re.M)
    bmc_ip = m.group(1) if m and m.group(1) != "0.0.0.0" else ""
    macs, speeds = {}, {}
    for line in sec.get("NIC", "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            macs[parts[0]] = parts[1]
            if len(parts) >= 3 and parts[2].lstrip("-").isdigit() and int(parts[2]) > 0:
                speeds[parts[0]] = int(parts[2])
    bmc_type = next((t for k, t in BMC_BY_VENDOR.items() if k in vendor.lower()), "BMC") if bmc_ip else ""
    return {
        "vendor": vendor,
        "model": dmi.get("system-product-name", "").strip(),
        "serial": dmi.get("system-serial-number", "").strip(),
        "bios_version": dmi.get("bios-version", "").strip(),
        "bios_date": dmi.get("bios-release-date", "").strip(),
        "bmc_ip": bmc_ip,
        "bmc_type": bmc_type,
        "lldp": parse_lldp(sec.get("LLDP", "")),
        "macs": macs,
        "speeds": speeds,
    }


def _load_key(secret):
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(secret))
        except Exception:
            continue
    raise ValueError("Clé privée SSH non reconnue (Ed25519, ECDSA ou RSA attendue)")


def _run(source, address, timeout=20):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    secret = source.ssh_secret
    kwargs = {"hostname": address, "port": source.ssh_port, "username": source.ssh_username, "timeout": timeout,
              "banner_timeout": timeout, "auth_timeout": timeout, "allow_agent": False, "look_for_keys": False}
    if secret.lstrip().startswith("-----BEGIN"):
        kwargs["pkey"] = _load_key(secret)
    else:
        kwargs["password"] = secret
    client.connect(**kwargs)
    try:
        _, stdout, _ = client.exec_command("sh -s", timeout=60)
        stdout.channel.sendall(SCRIPT.encode())
        stdout.channel.shutdown_write()
        return stdout.read().decode(errors="replace")
    finally:
        client.close()


def ssh_enrich(source, targets, log):
    """targets : {nom_du_nœud: adresse}. Retourne {nom_du_nœud: infos}."""

    def one(item):
        name, addr = item
        try:
            return name, parse_output(_run(source, addr))
        except Exception as exc:
            log.warn(f"{name} : enrichissement SSH impossible ({exc})")
            return name, {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, targets.items()))
