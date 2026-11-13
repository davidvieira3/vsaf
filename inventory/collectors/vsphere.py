"""Collecte vSphere façon RVTools, en lecture seule.

Toutes les propriétés sont lues en masse via le PropertyCollector (un aller-retour par type d'objet),
ce qui reste rapide même avec des milliers de VM par vCenter. Seule la découverte CDP/LLDP
(QueryNetworkHint, privilège System.Read) nécessite un appel par hôte.
"""
from collections import defaultdict

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim, vmodl

from core.tls import ssl_context

from .base import VmAggregate, pct, s

CLUSTER_PROPS = ["name", "parent", "summary", "configurationEx", "host"]
HOST_PROPS = [
    "name", "parent", "summary.hardware", "summary.config.product", "summary.runtime", "summary.quickStats",
    "hardware.systemInfo", "hardware.biosInfo", "config.hyperThread", "config.lockdownMode",
    "config.network.pnic", "config.network.vnic", "config.network.vswitch", "config.network.portgroup",
    "config.network.proxySwitch", "config.storageDevice.hostBusAdapter", "configManager.networkSystem",
]
VM_PROPS = [
    "name", "parent", "config.template", "config.uuid", "config.instanceUuid", "config.guestFullName",
    "config.version", "config.annotation", "config.hardware.numCPU", "config.hardware.numCoresPerSocket",
    "config.hardware.memoryMB", "runtime.powerState", "runtime.host", "guest.toolsRunningStatus",
    "guest.toolsVersionStatus2", "guest.toolsVersion", "guest.ipAddress", "guest.hostName", "guest.guestFullName",
    "summary.storage.committed", "summary.storage.uncommitted", "summary.quickStats.overallCpuUsage",
    "summary.quickStats.guestMemoryUsage", "datastore", "network",
]
DS_PROPS = ["name", "parent", "summary", "info", "host", "vm"]
DVS_PROPS = ["name", "parent", "uuid", "summary", "config", "portgroup"]
DVPG_PROPS = ["name", "key", "config", "vm"]


def retrieve(content, vimtype, props):
    """Lecture en masse d'un type d'objet via ContainerView + PropertyCollector."""
    view = content.viewManager.CreateContainerView(content.rootFolder, [vimtype], True)
    try:
        pc = content.propertyCollector
        traversal = vmodl.query.PropertyCollector.TraversalSpec(
            name="traverseView", path="view", skip=False, type=vim.view.ContainerView
        )
        obj_spec = vmodl.query.PropertyCollector.ObjectSpec(obj=view, skip=True, selectSet=[traversal])
        prop_spec = vmodl.query.PropertyCollector.PropertySpec(type=vimtype, pathSet=props, all=False)
        fspec = vmodl.query.PropertyCollector.FilterSpec(objectSet=[obj_spec], propSet=[prop_spec])
        opts = vmodl.query.PropertyCollector.RetrieveOptions(maxObjects=1000)
        out = []
        res = pc.RetrievePropertiesEx([fspec], opts)
        while res:
            for oc in res.objects:
                d = {p.name: p.val for p in (oc.propSet or [])}
                d["_obj"] = oc.obj
                d["_moid"] = oc.obj._moId
                out.append(d)
            if not res.token:
                break
            res = pc.ContinueRetrievePropertiesEx(res.token)
        return out
    finally:
        view.Destroy()


def moid(obj):
    return obj._moId if obj is not None else None


def g(obj, *path, default=None):
    """getattr en chaîne tolérant aux None."""
    for attr in path:
        if obj is None:
            return default
        obj = getattr(obj, attr, None)
    return default if obj is None else obj


def _vlan_text(vlan):
    if vlan is None:
        return ""
    if isinstance(vlan, vim.dvs.VmwareDistributedVirtualSwitch.TrunkVlanSpec):
        return "trunk " + ",".join(f"{r.start}-{r.end}" if r.start != r.end else str(r.start) for r in vlan.vlanId)
    if isinstance(vlan, vim.dvs.VmwareDistributedVirtualSwitch.PvlanSpec):
        return f"pvlan {vlan.pvlanId}"
    vid = getattr(vlan, "vlanId", None)
    return "" if vid in (None, 0) else str(vid)


class VSphereCollector:
    def __init__(self, source, log):
        self.source = source
        self.log = log
        self.si = None

    def connect(self):
        self.si = SmartConnect(
            host=self.source.host,
            port=self.source.port,
            user=self.source.username,
            pwd=self.source.secret,
            sslContext=ssl_context(self.source.verify_ssl),
            httpConnectionTimeout=120,
        )
        return self.si

    def close(self):
        if self.si is not None:
            try:
                Disconnect(self.si)
            except Exception:
                pass
            self.si = None

    def test(self):
        try:
            self.connect()
            about = self.si.RetrieveContent().about
            return f"{about.fullName} (instance {about.instanceUuid})"
        finally:
            self.close()

    # ------------------------------------------------------------------
    def collect(self):
        self.connect()
        try:
            return self._collect()
        finally:
            self.close()

    def _collect(self):
        log = self.log
        content = self.si.RetrieveContent()
        about = content.about
        log.info(f"Connecté : {about.fullName}")

        # Arborescence pour retrouver le datacenter de chaque objet
        tree = {}
        for t in (vim.Folder, vim.Datacenter, vim.ComputeResource):
            for o in retrieve(content, t, ["name", "parent"]):
                tree[o["_moid"]] = (o["_obj"], o.get("name"), moid(o.get("parent")))

        def dc_of(mo):
            seen = 0
            while mo and seen < 64:
                node = tree.get(mo)
                if node is None:
                    return ""
                if isinstance(node[0], vim.Datacenter):
                    return node[1]
                mo = node[2]
                seen += 1
            return ""

        networks = {o["_moid"]: o.get("name") for o in retrieve(content, vim.Network, ["name"])}

        # --- Clusters / ComputeResource ------------------------------------
        crs = retrieve(content, vim.ComputeResource, CLUSTER_PROPS)
        cluster_of_cr = {}
        for cr in crs:
            if isinstance(cr["_obj"], vim.ClusterComputeResource):
                cluster_of_cr[cr["_moid"]] = cr.get("name")
        log.info(f"{len(cluster_of_cr)} cluster(s)")

        # --- Hôtes -----------------------------------------------------------
        hosts_raw = retrieve(content, vim.HostSystem, HOST_PROPS)
        log.info(f"{len(hosts_raw)} hôte(s) ESXi")
        host_name = {h["_moid"]: h.get("name") for h in hosts_raw}
        host_cluster = {h["_moid"]: cluster_of_cr.get(moid(h.get("parent")), "") for h in hosts_raw}

        # --- Datastores ------------------------------------------------------
        ds_raw = retrieve(content, vim.Datastore, DS_PROPS)
        ds_name = {d["_moid"]: d.get("name") for d in ds_raw}

        # --- VM ----------------------------------------------------------------
        vm_props = VM_PROPS + (["config.hardware.device"] if self.source.collect_vm_devices else [])
        vms_raw = retrieve(content, vim.VirtualMachine, vm_props)
        log.info(f"{len(vms_raw)} VM / template(s)")

        links = []  # relations VM ↔ datastore et hôte ↔ datastore pour les filtres croisés
        host_agg, cluster_agg = VmAggregate(), VmAggregate()
        vms = []
        pg_vm_count = defaultdict(int)
        for v in vms_raw:
            template = bool(v.get("config.template"))
            hmo = moid(v.get("runtime.host"))
            cl = host_cluster.get(hmo, "")
            power = s(v.get("runtime.powerState"))
            vcpu = v.get("config.hardware.numCPU") or 0
            mem_mb = v.get("config.hardware.memoryMB") or 0
            if not template:
                host_agg.add(hmo, vcpu, mem_mb * 1048576, power == "poweredOn")
                if cl:
                    cluster_agg.add(cl, vcpu, mem_mb * 1048576, power == "poweredOn")
            nets = [networks.get(moid(n), "") for n in (v.get("network") or [])]
            for n in v.get("network") or []:
                pg_vm_count[moid(n)] += 1
            disks, nics, props = 0, 0, {}
            devices = v.get("config.hardware.device") or []
            if devices:
                disk_list, nic_list = [], []
                for dev in devices:
                    if isinstance(dev, vim.vm.device.VirtualDisk):
                        disk_list.append({
                            "label": g(dev, "deviceInfo", "label"),
                            "capacity_gb": round((dev.capacityInBytes or dev.capacityInKB * 1024) / 1024**3, 1),
                            "file": g(dev, "backing", "fileName"),
                            "thin": g(dev, "backing", "thinProvisioned"),
                            "mode": g(dev, "backing", "diskMode"),
                        })
                    elif isinstance(dev, vim.vm.device.VirtualEthernetCard):
                        nic_list.append({
                            "label": g(dev, "deviceInfo", "label"),
                            "type": type(dev).__name__.split(".")[-1],
                            "mac": dev.macAddress,
                            "connected": g(dev, "connectable", "connected"),
                            "network": g(dev, "deviceInfo", "summary"),
                        })
                disks, nics = len(disk_list), len(nic_list)
                props = {"disks": disk_list, "nics": nic_list}
            for d in v.get("datastore") or []:
                if ds_name.get(moid(d)):
                    links.append({"kind": "vm_ds", "left_moid": v["_moid"], "left_name": s(v.get("name")), "right_name": ds_name[moid(d)]})
            committed = v.get("summary.storage.committed") or 0
            vms.append({
                "moid": v["_moid"],
                "name": s(v.get("name")),
                "datacenter": "",
                "cluster_name": cl,
                "host_name": s(host_name.get(hmo)),
                "vm_id": v["_moid"],
                "kind": "template" if template else "vm",
                "power_state": power,
                "template": template,
                "guest_os": s(v.get("guest.guestFullName") or v.get("config.guestFullName")),
                "guest_hostname": s(v.get("guest.hostName")),
                "ip_address": s(v.get("guest.ipAddress"), 64),
                "vcpu": vcpu,
                "cores_per_socket": v.get("config.hardware.numCoresPerSocket"),
                "mem_mb": mem_mb,
                "hw_version": s(v.get("config.version"), 32),
                "tools_status": s(v.get("guest.toolsRunningStatus"), 64),
                "tools_version": s(v.get("guest.toolsVersionStatus2") or v.get("guest.toolsVersion"), 64),
                "provisioned_bytes": committed + (v.get("summary.storage.uncommitted") or 0),
                "used_bytes": committed,
                "num_disks": disks,
                "num_nics": nics,
                "networks": ", ".join(sorted(set(filter(None, nets)))),
                "datastores": ", ".join(sorted(ds_name.get(moid(d), "") for d in (v.get("datastore") or []))),
                "uuid": s(v.get("config.instanceUuid"), 64),
                "bios_uuid": s(v.get("config.uuid"), 64),
                "annotation": s(v.get("config.annotation"), 4000),
                "cpu_usage_mhz": v.get("summary.quickStats.overallCpuUsage"),
                "mem_usage_mb": v.get("summary.quickStats.guestMemoryUsage"),
                "props": props,
                "_hmo": hmo,
            })

        # datacenter des VM = celui de leur hôte (évite de parcourir les dossiers de VM)
        host_dc = {h["_moid"]: dc_of(moid(h.get("parent"))) for h in hosts_raw}
        for vm in vms:
            vm["datacenter"] = host_dc.get(vm.pop("_hmo"), "")

        hosts, neighbors, vswitches, portgroups = [], [], [], []
        dvs_uplinks_by_host = {}
        for h in hosts_raw:
            hw = h.get("summary.hardware")
            prod = h.get("summary.config.product")
            rt = h.get("summary.runtime")
            qs = h.get("summary.quickStats")
            sysinfo = h.get("hardware.systemInfo")
            bios = h.get("hardware.biosInfo")
            cores = g(hw, "numCpuCores", default=0)
            threads = g(hw, "numCpuThreads", default=0)
            mhz = g(hw, "cpuMhz", default=0)
            mem = g(hw, "memorySize", default=0)
            serial = self._serial(sysinfo)
            vnics = h.get("config.network.vnic") or []
            mgmt_ip = ""
            for vn in vnics:
                if vn.device == "vmk0":
                    mgmt_ip = g(vn, "spec", "ip", "ipAddress", default="")
            if not mgmt_ip and vnics:
                mgmt_ip = g(vnics[0], "spec", "ip", "ipAddress", default="")
            cpu_used = g(qs, "overallCpuUsage")
            mem_used = g(qs, "overallMemoryUsage")
            row = {
                "moid": h["_moid"],
                "name": s(h.get("name")),
                "datacenter": host_dc.get(h["_moid"], ""),
                "cluster_name": host_cluster.get(h["_moid"], ""),
                "mgmt_ip": s(mgmt_ip, 64),
                "product": s(g(prod, "fullName")),
                "version": s(g(prod, "version"), 64),
                "build": s(g(prod, "build"), 64),
                "vendor": s(g(hw, "vendor"), 128),
                "model": s(g(hw, "model"), 128),
                "serial": s(serial, 128),
                "bios_version": s(g(bios, "biosVersion"), 64),
                "bios_date": s(g(bios, "releaseDate").date() if g(bios, "releaseDate") else "", 32),
                "cpu_model": s(g(hw, "cpuModel")),
                "cpu_sockets": g(hw, "numCpuPkgs", default=0),
                "cpu_cores": cores,
                "cpu_threads": threads,
                "cpu_mhz": mhz,
                "mem_bytes": mem,
                "ht_active": g(h.get("config.hyperThread"), "active"),
                "connection_state": s(g(rt, "connectionState"), 32),
                "power_state": s(g(rt, "powerState"), 32),
                "maintenance": g(rt, "inMaintenanceMode"),
                "lockdown": s(h.get("config.lockdownMode"), 32),
                "boot_time": g(rt, "bootTime"),
                "cpu_usage_pct": pct(cpu_used, cores * mhz) if cpu_used is not None else None,
                "mem_usage_pct": pct((mem_used or 0) * 1048576, mem) if mem_used is not None else None,
                "num_nics": g(hw, "numNics", default=0),
                "num_hbas": g(hw, "numHBAs", default=0),
                "props": {
                    "vmkernel": [
                        {
                            "device": vn.device,
                            "portgroup": vn.portgroup,
                            "ip": g(vn, "spec", "ip", "ipAddress"),
                            "mask": g(vn, "spec", "ip", "subnetMask"),
                            "mtu": g(vn, "spec", "mtu"),
                            "mac": g(vn, "spec", "mac"),
                        }
                        for vn in vnics
                    ],
                    "hba": [
                        {"device": hba.device, "model": hba.model, "driver": hba.driver, "status": hba.status,
                         "wwpn": format(hba.portWorldWideName, "x") if isinstance(hba, vim.host.FibreChannelHba) else None,
                         "iqn": getattr(hba, "iScsiName", None)}
                        for hba in (h.get("config.storageDevice.hostBusAdapter") or [])
                    ],
                    "uptime_s": g(qs, "uptime"),
                },
            }
            row.update(host_agg.fields(h["_moid"], cores, mem))
            hosts.append(row)

            # vSwitch standard + portgroups standard
            pnic_to_switch = {}
            for vsw in h.get("config.network.vswitch") or []:
                ups = [k.split("key-vim.host.PhysicalNic-")[-1] for k in (vsw.pnic or [])]
                for u in ups:
                    pnic_to_switch[u] = vsw.name
                vswitches.append({
                    "moid": f"{h['_moid']}:{vsw.name}",
                    "name": vsw.name,
                    "datacenter": row["datacenter"],
                    "kind": "vSwitch standard",
                    "host_name": row["name"],
                    "mtu": vsw.mtu,
                    "num_uplinks": len(ups),
                    "uplinks": ", ".join(ups),
                    "num_hosts": 1,
                    "num_ports": vsw.numPorts or 0,
                    "num_portgroups": len(vsw.portgroup or []),
                })
            for pg in h.get("config.network.portgroup") or []:
                portgroups.append({
                    "moid": f"{h['_moid']}:{pg.spec.name}",
                    "name": pg.spec.name,
                    "switch_name": pg.spec.vswitchName,
                    "kind": "Standard",
                    "host_name": row["name"],
                    "vlan": str(pg.spec.vlanId) if pg.spec.vlanId else "",
                    "num_ports": len(pg.port or []),
                })
            for ps in h.get("config.network.proxySwitch") or []:
                for u in ps.pnic or []:
                    pnic_to_switch[u.split("key-vim.host.PhysicalNic-")[-1]] = ps.dvsName
            dvs_uplinks_by_host[h["_moid"]] = pnic_to_switch

            # Cartes physiques + CDP/LLDP
            hints = {}
            if self.source.collect_network_hints and g(rt, "connectionState") == "connected":
                try:
                    ns = h.get("configManager.networkSystem")
                    for hint in ns.QueryNetworkHint() or []:
                        hints[hint.device] = hint
                except Exception as exc:  # hôte injoignable, pas de permission...
                    log.warn(f"{row['name']} : CDP/LLDP indisponible ({exc.__class__.__name__}: {getattr(exc, 'msg', exc)})")
            for pnic in h.get("config.network.pnic") or []:
                neighbors.append(self._neighbor(row, pnic, hints.get(pnic.device), pnic_to_switch.get(pnic.device, "")))

        # --- Clusters (après les hôtes pour les agrégats) --------------------
        host_rows = {r["moid"]: r for r in hosts}
        clusters = []
        for cr in crs:
            if not isinstance(cr["_obj"], vim.ClusterComputeResource):
                continue
            summ = cr.get("summary")
            conf = cr.get("configurationEx")
            members = [host_rows[moid(x)] for x in (cr.get("host") or []) if moid(x) in host_rows]
            cores = sum(m["cpu_cores"] or 0 for m in members)
            mem = sum(m["mem_bytes"] or 0 for m in members)
            total_mhz = sum((m["cpu_cores"] or 0) * (m["cpu_mhz"] or 0) for m in members)
            cpu_used = sum((m["cpu_usage_pct"] or 0) / 100 * (m["cpu_cores"] or 0) * (m["cpu_mhz"] or 0) for m in members)
            mem_used = sum((m["mem_usage_pct"] or 0) / 100 * (m["mem_bytes"] or 0) for m in members)
            name = cr.get("name")
            row = {
                "moid": cr["_moid"],
                "name": s(name),
                "datacenter": dc_of(moid(cr.get("parent"))),
                "num_hosts": g(summ, "numHosts", default=len(members)),
                "num_hosts_effective": g(summ, "numEffectiveHosts", default=0),
                "total_cores": cores,
                "total_threads": sum(m["cpu_threads"] or 0 for m in members),
                "total_mhz": total_mhz,
                "total_mem_bytes": mem,
                "cpu_usage_pct": pct(cpu_used, total_mhz),
                "mem_usage_pct": pct(mem_used, mem),
                "ha_enabled": g(conf, "dasConfig", "enabled"),
                "ha_admission_control": g(conf, "dasConfig", "admissionControlEnabled"),
                "drs_enabled": g(conf, "drsConfig", "enabled"),
                "drs_behavior": s(g(conf, "drsConfig", "defaultVmBehavior"), 32),
                "vsan_enabled": g(conf, "vsanConfigInfo", "enabled"),
                "evc_mode": s(g(summ, "currentEVCModeKey"), 64),
                "props": {
                    "ha_host_monitoring": g(conf, "dasConfig", "hostMonitoring"),
                    "ha_failover_level": g(conf, "dasConfig", "failoverLevel"),
                    "admission_policy": type(g(conf, "dasConfig", "admissionControlPolicy")).__name__
                    if g(conf, "dasConfig", "admissionControlPolicy") else None,
                    "drs_vmotion_rate": g(conf, "drsConfig", "vmotionRate"),
                    "dpm_enabled": g(conf, "dpmConfigInfo", "enabled"),
                    "num_vmotions": g(summ, "numVmotions"),
                    "hosts": [m["name"] for m in members],
                },
            }
            row.update(cluster_agg.fields(name, cores, mem))
            clusters.append(row)

        # --- Datastores -----------------------------------------------------
        cluster_by_host_name = {r["name"]: r["cluster_name"] for r in hosts}
        datastores = []
        for d in ds_raw:
            summ = d.get("summary")
            info = d.get("info")
            cap = g(summ, "capacity", default=0)
            free = g(summ, "freeSpace", default=0)
            unc = g(summ, "uncommitted", default=0)
            dtype = s(g(summ, "type"), 32)
            mounts = d.get("host") or []
            mount_hosts = [host_name.get(moid(m.key), "") for m in mounts]
            for hn in filter(None, mount_hosts):
                links.append({"kind": "host_ds", "left_name": hn, "right_name": s(d.get("name"))})
            remote, fs_version, extents = "", "", ""
            vmfs = g(info, "vmfs")
            nas = g(info, "nas")
            if vmfs is not None:
                fs_version = s(vmfs.version, 32)
                extents = ", ".join(f"{e.diskName}:{e.partition}" for e in (vmfs.extent or []))
            if nas is not None:
                remote = s(f"{nas.remoteHost}:{nas.remotePath}", 512)
                fs_version = s(nas.type, 32)
            used = cap - free
            datastores.append({
                "moid": d["_moid"],
                "name": s(d.get("name")),
                "datacenter": dc_of(moid(d.get("parent"))),
                "ds_type": dtype,
                "capacity_bytes": cap,
                "free_bytes": free,
                "used_bytes": used,
                "provisioned_bytes": used + unc,
                "used_pct": pct(used, cap),
                "provisioned_pct": pct(used + unc, cap),
                "accessible": g(summ, "accessible"),
                "maintenance": s(g(summ, "maintenanceMode"), 32),
                "shared": g(summ, "multipleHostAccess"),
                "num_hosts": len(mounts),
                "num_vms": len(d.get("vm") or []),
                "fs_version": fs_version,
                "remote": remote,
                "extents": extents,
                "clusters": ", ".join(sorted({cluster_by_host_name.get(n, "") for n in mount_hosts} - {""})),
                "url": s(g(summ, "url"), 512),
                "props": {"hosts": sorted(filter(None, mount_hosts))},
            })

        # --- Distributed switches & portgroups ------------------------------
        dvs_raw = retrieve(content, vim.DistributedVirtualSwitch, DVS_PROPS)
        dvs_name = {}
        for d in dvs_raw:
            conf = d.get("config")
            summ = d.get("summary")
            dvs_name[d["_moid"]] = d.get("name")
            ldp = g(conf, "linkDiscoveryProtocolConfig")
            uplinks = list(g(conf, "uplinkPortPolicy", "uplinkPortName", default=[]) or [])
            members = g(conf, "host", default=[]) or []
            vswitches.append({
                "moid": d["_moid"],
                "name": s(d.get("name")),
                "datacenter": dc_of(moid(d.get("parent"))),
                "kind": "vDS",
                "version": s(g(summ, "productInfo", "version"), 32),
                "vendor": s(g(summ, "productInfo", "vendor"), 64),
                "mtu": g(conf, "maxMtu"),
                "num_uplinks": len(uplinks),
                "uplinks": ", ".join(uplinks),
                "num_hosts": len(members),
                "num_ports": g(summ, "numPorts", default=0),
                "num_portgroups": len(d.get("portgroup") or []),
                "discovery": f"{g(ldp, 'protocol', default='')} {g(ldp, 'operation', default='')}".strip(),
                "lacp": s(g(conf, "lacpApiVersion"), 64),
                "nioc": g(conf, "networkResourceManagementEnabled"),
                "props": {
                    "uuid": d.get("uuid"),
                    "description": g(conf, "description"),
                    "contact": g(conf, "contact", "name"),
                    "lag": [
                        {"name": lag.name, "mode": lag.mode, "uplinks": lag.uplinkNum, "lb": lag.loadbalanceAlgorithm}
                        for lag in (g(conf, "lacpGroupConfig", default=[]) or [])
                    ],
                    "hosts": [
                        {"host": host_name.get(moid(m.config.host), ""),
                         "pnics": [p.pnicDevice for p in (g(m, "config", "backing", "pnicSpec", default=[]) or [])]}
                        for m in members
                    ],
                },
            })
        for pg in retrieve(content, vim.dvs.DistributedVirtualPortgroup, DVPG_PROPS):
            conf = pg.get("config")
            portgroups.append({
                "moid": pg["_moid"],
                "name": s(pg.get("name")),
                "switch_name": s(dvs_name.get(moid(g(conf, "distributedVirtualSwitch")), "")),
                "kind": "Distribué",
                "vlan": s(_vlan_text(g(conf, "defaultPortConfig", "vlan"))),
                "num_ports": g(conf, "numPorts", default=0),
                "binding": s(g(conf, "type"), 32),
                "uplink": bool(g(conf, "uplink", default=False)),
                "num_vms": len(pg.get("vm") or []),
                "props": {
                    "key": pg.get("key"),
                    "teaming": g(conf, "defaultPortConfig", "uplinkTeamingPolicy", "policy", "value"),
                    "active_uplinks": list(g(conf, "defaultPortConfig", "uplinkTeamingPolicy", "uplinkPortOrder", "activeUplinkPort", default=[]) or []),
                    "standby_uplinks": list(g(conf, "defaultPortConfig", "uplinkTeamingPolicy", "uplinkPortOrder", "standbyUplinkPort", default=[]) or []),
                    "promiscuous": g(conf, "defaultPortConfig", "securityPolicy", "allowPromiscuous", "value"),
                    "forged_transmits": g(conf, "defaultPortConfig", "securityPolicy", "forgedTransmits", "value"),
                    "mac_changes": g(conf, "defaultPortConfig", "securityPolicy", "macChanges", "value"),
                },
            })
        vms_by_net_name = defaultdict(int)
        for net_moid, count in pg_vm_count.items():
            vms_by_net_name[networks.get(net_moid, "")] += count
        for pg in portgroups:
            if pg["kind"] == "Standard":
                pg["num_vms"] = vms_by_net_name.get(pg["name"], 0)  # total sur le vCenter pour ce nom de réseau

        datacenters = []
        dc_rows = defaultdict(lambda: {"num_clusters": 0, "num_hosts": 0, "num_vms": 0})
        for c in clusters:
            dc_rows[c["datacenter"]]["num_clusters"] += 1
        for h in hosts:
            dc_rows[h["datacenter"]]["num_hosts"] += 1
        for v in vms:
            dc_rows[v["datacenter"]]["num_vms"] += 1
        for mo, (obj, name, _) in tree.items():
            if isinstance(obj, vim.Datacenter):
                datacenters.append({"moid": mo, "name": s(name), **dc_rows[name]})

        about_fields = {
            "product_name": s(about.fullName),
            "version": s(about.version, 64),
            "build": s(about.build, 64),
            "api_version": s(about.apiVersion, 64),
            "instance_uuid": s(about.instanceUuid, 64),
            "info": {
                "os_type": about.osType,
                "product_line": about.productLineId,
                "license_product": about.licenseProductName,
                "license_version": about.licenseProductVersion,
                "datacenters": [d["name"] for d in datacenters],
            },
        }
        result = {
            "datacenters": datacenters,
            "clusters": clusters,
            "hosts": hosts,
            "datastores": datastores,
            "vswitches": vswitches,
            "portgroups": portgroups,
            "neighbors": neighbors,
            "vms": vms,
            "links": links,
        }
        return result, about_fields

    # ------------------------------------------------------------------
    @staticmethod
    def _serial(sysinfo):
        if sysinfo is None:
            return ""
        serial = getattr(sysinfo, "serialNumber", None)
        if serial and serial.strip() not in ("", "None", "Not Specified"):
            return serial.strip()
        found = {}
        for oi in getattr(sysinfo, "otherIdentifyingInfo", None) or []:
            key = g(oi, "identifierType", "key")
            val = (oi.identifierValue or "").strip()
            if val and val not in ("None", "Not Specified", "Unknown"):
                found.setdefault(key, val)
        for key in ("ServiceTag", "SerialNumberTag", "EnclosureSerialNumberTag", "AssetTag"):
            if key in found:
                return found[key]
        return ""

    @staticmethod
    def _neighbor(host_row, pnic, hint, vswitch):
        speed = g(pnic, "linkSpeed", "speedMb")
        row = {
            "moid": f"{host_row['moid']}:{pnic.device}",
            "name": host_row["name"],
            "cluster_name": host_row["cluster_name"],
            "nic": pnic.device,
            "mac": s(pnic.mac, 32),
            "link_speed_mb": speed,
            "driver": s(pnic.driver, 64),
            "vswitch": s(vswitch),
            "props": {"pci": pnic.pci, "link_up": speed is not None},
        }
        if hint is None:
            return row
        cdp = g(hint, "connectedSwitchPort")
        lldp = g(hint, "lldpInfo")
        if cdp is not None:
            row.update({
                "protocol": "CDP",
                "switch_name": s(cdp.devId or cdp.systemName),
                "switch_port": s(cdp.portId),
                "switch_ip": s(cdp.address or cdp.mgmtAddr, 64),
                "switch_platform": s(cdp.hardwarePlatform),
                "switch_vlan": s(cdp.vlan, 32) if cdp.vlan else "",
                "switch_mtu": cdp.mtu or None,
            })
            row["props"]["cdp_software"] = cdp.softwareVersion
            row["props"]["cdp_location"] = cdp.location
        elif lldp is not None:
            params = {p.key: p.value for p in (lldp.parameter or [])}
            row.update({
                "protocol": "LLDP",
                "switch_name": s(params.get("System Name") or lldp.chassisId),
                "switch_port": s(lldp.portId),
                "switch_port_descr": s(params.get("Port Description")),
                "switch_ip": s(params.get("Management Address"), 64),
                "switch_platform": s(params.get("System Description")),
                "switch_vlan": s(params.get("Vlan ID"), 32),
            })
            row["props"]["lldp"] = {k: str(v) for k, v in params.items()}
            row["props"]["chassis_id"] = lldp.chassisId
        return row
