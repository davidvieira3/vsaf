"""Collecte Proxmox VE via l'API REST (rôle PVEAuditor suffisant).

Mapping vers le modèle commun :
  Source = un cluster Proxmox (ou un nœud seul) | Cluster = cluster PVE | Host = nœud
  Datastore = stockage PVE (dédoublonné s'il est partagé) | VSwitch = bridge / bond / OVS / zone SDN
  PortGroup = VNet SDN ou VLAN Linux | VM = qemu + lxc
"""
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from proxmoxer import ProxmoxAPI

from core.tls import requests_verify

from .base import VmAggregate, pct, s
from .proxmox_ssh import ssh_enrich

DISK_KEY = re.compile(r"^(scsi|virtio|sata|ide|mp|efidisk|tpmstate)\d+$|^rootfs$")


class ProxmoxCollector:
    def __init__(self, source, log):
        self.source = source
        self.log = log
        self.api = None

    def connect(self):
        src = self.source
        kwargs = {"port": src.port, "verify_ssl": requests_verify(src.verify_ssl), "timeout": 30}
        if src.token_id:
            self.api = ProxmoxAPI(src.host, user=src.username, token_name=src.token_id, token_value=src.secret, **kwargs)
        else:
            self.api = ProxmoxAPI(src.host, user=src.username, password=src.secret, **kwargs)
        return self.api

    def test(self):
        api = self.connect()
        v = api.version.get()
        return f"Proxmox VE {v.get('version')} (release {v.get('release')})"

    def _try(self, label, fn, default=None):
        try:
            return fn()
        except Exception as exc:
            self.log.warn(f"{label} : {exc}")
            return default

    def collect(self):
        api = self.connect()
        log = self.log
        version = api.version.get()
        log.info(f"Connecté : Proxmox VE {version.get('version')}")

        status = api.cluster.status.get()
        cluster_entry = next((x for x in status if x.get("type") == "cluster"), None)
        node_entries = {x["name"]: x for x in status if x.get("type") == "node"}
        cluster_name = cluster_entry["name"] if cluster_entry else ""
        resources = api.cluster.resources.get()
        nodes_res = [r for r in resources if r.get("type") == "node"]
        guests = [r for r in resources if r.get("type") in ("qemu", "lxc")]
        storages_res = [r for r in resources if r.get("type") == "storage"]
        log.info(f"Cluster « {cluster_name or 'nœud seul'} » : {len(nodes_res)} nœud(s), {len(guests)} invité(s)")

        storage_cfg = {x["storage"]: x for x in self._try("Configuration des stockages", api.storage.get, [])}

        # --- VM / CT ------------------------------------------------------------
        details = {}
        if self.source.collect_vm_devices:
            def fetch(g):
                path = getattr(api.nodes(g["node"]), g["type"])(g["vmid"])
                try:
                    return g["id"], path.config.get()
                except Exception:
                    return g["id"], {}
            with ThreadPoolExecutor(max_workers=8) as pool:
                details = dict(pool.map(fetch, [g for g in guests if g.get("node")]))

        node_agg = VmAggregate()
        vms = []
        for gst in guests:
            cfg = details.get(gst["id"], {})
            template = bool(gst.get("template"))
            vcpu = int(gst.get("maxcpu") or 0)
            mem_mb = int((gst.get("maxmem") or 0) / 1048576)
            running = gst.get("status") == "running"
            if not template:
                node_agg.add(gst.get("node"), vcpu, gst.get("maxmem") or 0, running)
            disks = [k for k in cfg if DISK_KEY.match(k)]
            disks = [k for k in disks if "media=cdrom" not in str(cfg.get(k, ""))]
            nets = {k: v for k, v in cfg.items() if k.startswith("net") and k[3:].isdigit()}
            bridges = sorted({part.split("=", 1)[1] for v in nets.values() for part in str(v).split(",") if part.startswith("bridge=")})
            storages = sorted({str(cfg[k]).split(":", 1)[0] for k in disks if ":" in str(cfg[k])})
            vms.append({
                "moid": gst["id"],
                "name": s(gst.get("name") or gst["id"]),
                "datacenter": "",
                "cluster_name": cluster_name,
                "host_name": s(gst.get("node")),
                "vm_id": str(gst.get("vmid")),
                "kind": "template" if template else ("ct" if gst["type"] == "lxc" else "vm"),
                "power_state": s(gst.get("status"), 32),
                "template": template,
                "guest_os": s(cfg.get("ostype")),
                "vcpu": vcpu,
                "cores_per_socket": int(cfg["cores"]) if str(cfg.get("cores", "")).isdigit() else None,
                "mem_mb": mem_mb,
                "hw_version": s(cfg.get("machine"), 32),
                "tools_status": "agent activé" if str(cfg.get("agent", "")).startswith(("1", "enabled=1")) else "",
                "provisioned_bytes": int(gst.get("maxdisk") or 0),
                "used_bytes": int(gst.get("disk") or 0),
                "num_disks": len(disks),
                "num_nics": len(nets),
                "networks": ", ".join(bridges),
                "datastores": ", ".join(storages),
                "uuid": "",
                "bios_uuid": s(str(cfg.get("smbios1", "")).split("uuid=")[-1].split(",")[0] if "uuid=" in str(cfg.get("smbios1", "")) else "", 64),
                "annotation": s(cfg.get("description"), 4000),
                "tags": s(gst.get("tags"), 500),
                "cpu_usage_mhz": None,
                "mem_usage_mb": int((gst.get("mem") or 0) / 1048576) if running else None,
                "props": {"pool": gst.get("pool"), "ha": gst.get("hastate"), "config": {k: str(v) for k, v in cfg.items() if k != "description"}},
            })

        # --- Nœuds ------------------------------------------------------------
        hosts, vswitches, portgroups, neighbors = [], [], [], []
        node_names = [n["node"] for n in nodes_res]

        def node_detail(name):
            st = self._try(f"{name} : statut", lambda: api.nodes(name).status.get(), {})
            net = self._try(f"{name} : réseau", lambda: api.nodes(name).network.get(), [])
            return name, st, net

        with ThreadPoolExecutor(max_workers=8) as pool:
            node_data = {name: (st, net) for name, st, net in pool.map(node_detail, node_names)}

        ssh_data = {}
        if self.source.ssh_enabled:
            targets = {n: node_entries.get(n, {}).get("ip") or n for n in node_names}
            ssh_data = ssh_enrich(self.source, targets, log)

        for n in nodes_res:
            name = n["node"]
            st, net = node_data.get(name, ({}, []))
            cpuinfo = st.get("cpuinfo", {}) if st else {}
            sockets = int(cpuinfo.get("sockets") or 0)
            cores = int(cpuinfo.get("cores") or 0) * (sockets or 1)
            threads = int(cpuinfo.get("cpus") or n.get("maxcpu") or 0)
            mem = int((st.get("memory") or {}).get("total") or n.get("maxmem") or 0)
            mem_used = int((st.get("memory") or {}).get("used") or n.get("mem") or 0)
            pve = str(st.get("pveversion", ""))  # pve-manager/8.2.4/faa83925c9641325
            parts = pve.split("/")
            hw = ssh_data.get(name, {})
            row = {
                "moid": f"node/{name}",
                "name": name,
                "datacenter": "",
                "cluster_name": cluster_name,
                "mgmt_ip": s(node_entries.get(name, {}).get("ip"), 64),
                "product": s(parts[0] if parts else "pve-manager"),
                "version": s(parts[1] if len(parts) > 1 else "", 64),
                "build": s(parts[2] if len(parts) > 2 else "", 64),
                "kernel": s(st.get("kversion") or (st.get("current-kernel") or {}).get("release"), 128),
                "vendor": s(hw.get("vendor"), 128),
                "model": s(hw.get("model"), 128),
                "serial": s(hw.get("serial"), 128),
                "bios_version": s(hw.get("bios_version"), 64),
                "bios_date": s(hw.get("bios_date"), 32),
                "cpu_model": s(cpuinfo.get("model")),
                "cpu_sockets": sockets,
                "cpu_cores": cores,
                "cpu_threads": threads,
                "cpu_mhz": int(float(cpuinfo.get("mhz") or 0)),
                "mem_bytes": mem,
                "ht_active": (threads > cores) if cores else None,
                "connection_state": "online" if n.get("status") == "online" else s(n.get("status"), 32),
                "power_state": s(n.get("status"), 32),
                "cpu_usage_pct": round(100 * float(n.get("cpu") or 0), 1) if n.get("status") == "online" else None,
                "mem_usage_pct": pct(mem_used, mem),
                "num_nics": sum(1 for i in net if i.get("type") == "eth"),
                "bmc_type": s(hw.get("bmc_type"), 64),
                "bmc_ip": s(hw.get("bmc_ip"), 64),
                "bmc_source": "ssh ipmitool" if hw.get("bmc_ip") else "",
                "props": {
                    "uptime_s": n.get("uptime"),
                    "loadavg": st.get("loadavg"),
                    "boot_mode": (st.get("boot-info") or {}).get("mode"),
                    "secureboot": (st.get("boot-info") or {}).get("secureboot"),
                    "rootfs": st.get("rootfs"),
                    "interfaces": [
                        {k: i.get(k) for k in ("iface", "type", "address", "cidr", "gateway", "active", "mtu", "comments")}
                        for i in net
                    ],
                },
            }
            row.update(node_agg.fields(name, cores, mem))
            if n.get("status") != "online":
                row["connection_state"] = "offline"
            hosts.append(row)

            port_to_bridge = {}
            for i in net:
                itype = i.get("type", "")
                if itype in ("bridge", "OVSBridge"):
                    ports = (i.get("bridge_ports") or i.get("ovs_ports") or "").split()
                    for p in ports:
                        port_to_bridge[p] = i["iface"]
                    vswitches.append({
                        "moid": f"{name}:{i['iface']}", "name": i["iface"], "kind": "OVS bridge" if itype == "OVSBridge" else "Linux bridge",
                        "host_name": name, "mtu": int(i["mtu"]) if str(i.get("mtu", "")).isdigit() else None,
                        "num_uplinks": len(ports), "uplinks": ", ".join(ports), "num_hosts": 1,
                        "address": s(i.get("cidr") or i.get("address"), 128),
                        "props": {"vlan_aware": i.get("bridge_vlan_aware"), "vids": i.get("bridge_vids"), "active": i.get("active"), "comments": i.get("comments")},
                    })
                elif itype in ("bond", "OVSBond"):
                    slaves = (i.get("slaves") or i.get("ovs_bonds") or "").split()
                    vswitches.append({
                        "moid": f"{name}:{i['iface']}", "name": i["iface"], "kind": "Bond",
                        "host_name": name, "mtu": int(i["mtu"]) if str(i.get("mtu", "")).isdigit() else None,
                        "num_uplinks": len(slaves), "uplinks": ", ".join(slaves), "num_hosts": 1,
                        "lacp": s(i.get("bond_mode") or i.get("bond-mode"), 64),
                        "props": {"hash_policy": i.get("bond-xmit-hash-policy") or i.get("bond_xmit_hash_policy"), "primary": i.get("bond-primary")},
                    })
                elif itype == "vlan":
                    portgroups.append({
                        "moid": f"{name}:{i['iface']}", "name": i["iface"], "switch_name": s(i.get("vlan-raw-device") or i["iface"].split(".")[0]),
                        "kind": "VLAN Linux", "host_name": name, "vlan": s(i.get("vlan-id") or i["iface"].split(".")[-1]),
                        "props": {"address": i.get("cidr")},
                    })
            # bonds membres d'un bridge : remonter le bridge sur les interfaces physiques
            for i in net:
                if i.get("type") in ("bond", "OVSBond"):
                    br = port_to_bridge.get(i["iface"], "")
                    for sl in (i.get("slaves") or i.get("ovs_bonds") or "").split():
                        port_to_bridge.setdefault(sl, f"{br} via {i['iface']}" if br else i["iface"])
            lldp = hw.get("lldp", {})
            for i in net:
                if i.get("type") != "eth":
                    continue
                nb = lldp.get(i["iface"], {})
                neighbors.append({
                    "moid": f"{name}:{i['iface']}", "name": name, "cluster_name": cluster_name, "nic": i["iface"],
                    "mac": s(hw.get("macs", {}).get(i["iface"]), 32), "link_speed_mb": hw.get("speeds", {}).get(i["iface"]),
                    "vswitch": s(port_to_bridge.get(i["iface"], "")),
                    "protocol": nb.get("protocol", ""), "switch_name": s(nb.get("switch_name")),
                    "switch_port": s(nb.get("switch_port")), "switch_port_descr": s(nb.get("switch_port_descr")),
                    "switch_ip": s(nb.get("switch_ip"), 64), "switch_platform": s(nb.get("switch_platform")),
                    "switch_vlan": s(nb.get("switch_vlan"), 32),
                    "props": {"active": i.get("active"), "lldp": nb.get("raw")},
                })

        # --- SDN ----------------------------------------------------------------
        zones = self._try("SDN zones", lambda: api.cluster.sdn.zones.get(), []) or []
        vnets = self._try("SDN vnets", lambda: api.cluster.sdn.vnets.get(), []) or []
        vnet_count = defaultdict(int)
        for vn in vnets:
            vnet_count[vn.get("zone")] += 1
            portgroups.append({
                "moid": f"vnet/{vn['vnet']}", "name": vn["vnet"], "switch_name": s(vn.get("zone")), "kind": "VNet SDN",
                "vlan": s(vn.get("tag") or ""), "props": {"alias": vn.get("alias"), "vlanaware": vn.get("vlanaware")},
            })
        for z in zones:
            vswitches.append({
                "moid": f"zone/{z['zone']}", "name": z["zone"], "kind": f"Zone SDN ({z.get('type')})",
                "num_hosts": len((z.get("nodes") or "").split(",")) if z.get("nodes") else len(hosts),
                "mtu": int(z["mtu"]) if str(z.get("mtu", "")).isdigit() else None,
                "num_portgroups": vnet_count.get(z["zone"], 0),
                "uplinks": s(z.get("bridge") or ""),
                "props": {k: v for k, v in z.items() if k not in ("zone", "digest")},
            })

        # --- Stockages ----------------------------------------------------------
        datastores = []
        by_storage = defaultdict(list)
        for r in storages_res:
            by_storage[r["storage"]].append(r)
        vm_per_storage = defaultdict(int)
        for vm in vms:
            for st_name in filter(None, vm["datastores"].split(", ")):
                vm_per_storage[st_name] += 1
        for name, entries in by_storage.items():
            cfg = storage_cfg.get(name, {})
            shared = bool(entries[0].get("shared")) or bool(cfg.get("shared"))
            groups = [entries] if shared else [[e] for e in entries]
            for grp in groups:
                e = grp[0]
                cap = int(e.get("maxdisk") or 0)
                used = int(e.get("disk") or 0)
                remote = ""
                if cfg.get("server"):
                    remote = f"{cfg.get('server')}:{cfg.get('export') or cfg.get('share') or cfg.get('target') or ''}"
                elif cfg.get("monhost"):
                    remote = f"ceph {cfg.get('monhost')} pool={cfg.get('pool', '')}"
                elif cfg.get("pool"):
                    remote = f"pool={cfg['pool']}"
                elif cfg.get("path"):
                    remote = cfg["path"]
                datastores.append({
                    "moid": f"storage/{name}" + ("" if shared else f"/{e.get('node')}"),
                    "name": name,
                    "ds_type": s(e.get("plugintype") or cfg.get("type"), 32),
                    "capacity_bytes": cap,
                    "free_bytes": max(cap - used, 0),
                    "used_bytes": used,
                    "provisioned_bytes": used,
                    "used_pct": pct(used, cap),
                    "provisioned_pct": pct(used, cap),
                    "accessible": all(x.get("status") == "available" for x in grp),
                    "shared": shared,
                    "num_hosts": len(grp),
                    "num_vms": vm_per_storage.get(name, 0),
                    "remote": s(remote, 512),
                    "clusters": cluster_name,
                    "node": "" if shared else s(e.get("node")),
                    "props": {"content": cfg.get("content") or e.get("content"), "nodes": [x.get("node") for x in grp],
                              "options": {k: v for k, v in cfg.items() if k not in ("digest",)}},
                })

        # --- Cluster ------------------------------------------------------------
        clusters = []
        if cluster_entry:
            cores = sum(h["cpu_cores"] for h in hosts)
            mem = sum(h["mem_bytes"] for h in hosts)
            cl_agg = VmAggregate()
            for vm in vms:
                if not vm["template"]:
                    cl_agg.add("c", vm["vcpu"], vm["mem_mb"] * 1048576, vm["power_state"] == "running")
            ha = self._try("HA", lambda: api.cluster.ha.resources.get(), []) or []
            row = {
                "moid": f"cluster/{cluster_name}",
                "name": cluster_name,
                "num_hosts": len(hosts),
                "num_hosts_effective": sum(1 for h in hosts if h["connection_state"] == "online"),
                "total_cores": cores,
                "total_threads": sum(h["cpu_threads"] for h in hosts),
                "total_mhz": sum(h["cpu_cores"] * h["cpu_mhz"] for h in hosts),
                "total_mem_bytes": mem,
                "cpu_usage_pct": pct(sum((h["cpu_usage_pct"] or 0) * h["cpu_threads"] for h in hosts), 100 * sum(h["cpu_threads"] for h in hosts)),
                "mem_usage_pct": pct(sum((h["mem_usage_pct"] or 0) * h["mem_bytes"] for h in hosts), 100 * mem),
                "ha_enabled": bool(ha),
                "quorate": bool(cluster_entry.get("quorate")),
                "props": {"version": cluster_entry.get("version"), "ha_resources": len(ha), "nodes": [h["name"] for h in hosts]},
            }
            row.update(cl_agg.fields("c", cores, mem))
            clusters.append(row)

        about = {
            "product_name": f"Proxmox VE {version.get('version')}",
            "version": s(version.get("version"), 64),
            "build": s(version.get("repoid"), 64),
            "api_version": s(version.get("release"), 64),
            "instance_uuid": "",
            "info": {"cluster": cluster_name, "nodes": node_names},
        }
        return {
            "datacenters": [],
            "clusters": clusters,
            "hosts": hosts,
            "datastores": datastores,
            "vswitches": vswitches,
            "portgroups": portgroups,
            "neighbors": neighbors,
            "vms": vms,
        }, about
