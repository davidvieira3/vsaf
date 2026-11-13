"""Vues tabulaires : une définition par vue, un seul moteur générique (pagination, tri, filtres, export)."""
import re
from dataclasses import dataclass, field, replace

from django.db.models import Exists, OuterRef, Q
from django.db.models.fields.json import KeyTextTransform

from .models import (
    Cluster, CustomField, Datastore, Host, InventoryLink, NetworkNeighbor, PortGroup, Source, StoragePool, StorageSystem,
    StorageVolume, VirtualMachine, VSwitch,
)


@dataclass
class Col:
    field: str
    title: str
    type: str = "text"  # text, int, float, bytes, mb, pct, cpu_ratio, mem_ratio, ds_pct, bool, datetime, status, platform
    visible: bool = True
    width: int | None = None
    frozen: bool = False


@dataclass
class Grid:
    key: str
    title: str
    model: type
    columns: list
    default_sort: str = "name"
    intro: str = ""
    platform_field: str | None = "source__platform"
    scope: str = "inventory"  # "inventory" (vCenter/Proxmox) ou "storage" (baies)
    extra_filters: dict = field(default_factory=dict)
    annotations: dict = field(default_factory=dict)  # colonnes calculées (champs personnalisés)

    @property
    def fields(self):
        return [c.field for c in self.columns]

    def col(self, name):
        return next((c for c in self.columns if c.field == name), None)


SRC = [Col("source__name", "Source", width=150), Col("source__platform", "Plateforme", "platform", width=90)]
RATIOS = [
    Col("vm_count", "VM", "int"),
    Col("vm_on", "VM allumées", "int"),
    Col("vcpu_on", "vCPU allumés", "int"),
    Col("vcpu_all", "vCPU total", "int", visible=False),
    Col("cpu_ratio_on", "vCPU/cœur (allumées)", "cpu_ratio"),
    Col("cpu_ratio_all", "vCPU/cœur (toutes)", "cpu_ratio"),
    Col("vram_on_bytes", "vRAM allumées", "bytes", visible=False),
    Col("vram_all_bytes", "vRAM totale", "bytes", visible=False),
    Col("mem_ratio_on", "vRAM/RAM (allumées)", "mem_ratio"),
    Col("mem_ratio_all", "vRAM/RAM (toutes)", "mem_ratio"),
]

GRIDS = {
    "sources": Grid(
        "sources", "vCenter et Proxmox", Source,
        [
            Col("name", "Nom", frozen=True, width=170),
            Col("platform", "Plateforme", "platform", width=90),
            Col("site", "Site"),
            Col("host", "Adresse"),
            Col("product_name", "Produit", width=220),
            Col("version", "Version"),
            Col("build", "Build"),
            Col("api_version", "API", visible=False),
            Col("num_datacenters", "Datacenters", "int"),
            Col("num_clusters", "Clusters", "int"),
            Col("num_hosts", "Hôtes", "int"),
            Col("num_vms", "VM", "int"),
            Col("num_vms_on", "VM allumées", "int"),
            Col("num_datastores", "Datastores", "int"),
            Col("total_cores", "Cœurs physiques", "int"),
            Col("total_threads", "Threads", "int", visible=False),
            Col("total_mem_bytes", "RAM physique", "bytes"),
            Col("vcpu_on", "vCPU allumés", "int"),
            Col("cpu_ratio_on", "vCPU/cœur (allumées)", "cpu_ratio"),
            Col("cpu_ratio_all", "vCPU/cœur (toutes)", "cpu_ratio"),
            Col("vram_on_bytes", "vRAM allumées", "bytes", visible=False),
            Col("mem_ratio_on", "vRAM/RAM (allumées)", "mem_ratio"),
            Col("mem_ratio_all", "vRAM/RAM (toutes)", "mem_ratio"),
            Col("storage_capacity_bytes", "Stockage total", "bytes"),
            Col("storage_free_bytes", "Stockage libre", "bytes"),
            Col("instance_uuid", "UUID d'instance", visible=False),
            Col("last_status", "Dernière collecte", "status"),
            Col("last_success_at", "Dernier succès", "datetime"),
            Col("last_duration_s", "Durée (s)", "float", visible=False),
            Col("last_error", "Erreur", visible=False),
        ],
        platform_field="platform",
        intro="Une ligne par vCenter ou cluster Proxmox, avec ses versions et ses ratios de consolidation.",
    ),
    "clusters": Grid(
        "clusters", "Clusters", Cluster,
        [Col("name", "Cluster", frozen=True, width=170)] + SRC + [
            Col("datacenter", "Datacenter"),
            Col("num_hosts", "Hôtes", "int"),
            Col("num_hosts_effective", "Hôtes actifs", "int"),
            Col("total_cores", "Cœurs physiques", "int"),
            Col("total_threads", "Threads", "int", visible=False),
            Col("total_mem_bytes", "RAM physique", "bytes"),
            Col("cpu_usage_pct", "CPU utilisé", "pct"),
            Col("mem_usage_pct", "RAM utilisée", "pct"),
        ] + RATIOS + [
            Col("ha_enabled", "HA", "bool"),
            Col("ha_admission_control", "Contrôle d'admission", "bool", visible=False),
            Col("drs_enabled", "DRS", "bool"),
            Col("drs_behavior", "Mode DRS", visible=False),
            Col("vsan_enabled", "vSAN", "bool"),
            Col("evc_mode", "EVC"),
            Col("quorate", "Quorum (PVE)", "bool", visible=False),
        ],
    ),
    "hosts": Grid(
        "hosts", "Hôtes ESXi et nœuds Proxmox", Host,
        [Col("name", "Hôte", frozen=True, width=190)] + SRC + [
            Col("datacenter", "Datacenter", visible=False),
            Col("cluster_name", "Cluster"),
            Col("mgmt_ip", "IP de gestion"),
            Col("connection_state", "État", "status"),
            Col("maintenance", "Maintenance", "bool"),
            Col("version", "Version"),
            Col("build", "Build"),
            Col("kernel", "Noyau", visible=False),
            Col("vendor", "Constructeur"),
            Col("model", "Modèle"),
            Col("serial", "N° de série"),
            Col("bmc_type", "BMC"),
            Col("bmc_ip", "IP BMC"),
            Col("bmc_name", "Nom BMC", visible=False),
            Col("bmc_firmware", "Firmware BMC"),
            Col("bmc_health", "Santé matériel", visible=False),
            Col("bmc_source", "Source BMC", visible=False),
            Col("bios_version", "BIOS"),
            Col("bios_date", "Date BIOS", visible=False),
            Col("cpu_model", "Processeur", width=220),
            Col("cpu_sockets", "Sockets", "int"),
            Col("cpu_cores", "Cœurs", "int"),
            Col("cpu_threads", "Threads", "int"),
            Col("ht_active", "HT", "bool", visible=False),
            Col("cpu_mhz", "MHz", "int", visible=False),
            Col("mem_bytes", "RAM", "bytes"),
            Col("cpu_usage_pct", "CPU utilisé", "pct"),
            Col("mem_usage_pct", "RAM utilisée", "pct"),
        ] + RATIOS + [
            Col("num_nics", "Cartes réseau", "int", visible=False),
            Col("num_hbas", "HBA", "int", visible=False),
            Col("lockdown", "Lockdown", visible=False),
            Col("boot_time", "Démarré le", "datetime", visible=False),
        ],
    ),
    "datastores": Grid(
        "datastores", "Stockage", Datastore,
        [Col("name", "Datastore", frozen=True, width=200)] + SRC + [
            Col("datacenter", "Datacenter", visible=False),
            Col("ds_type", "Type"),
            Col("fs_version", "Version FS"),
            Col("capacity_bytes", "Capacité", "bytes"),
            Col("used_bytes", "Utilisé", "bytes"),
            Col("free_bytes", "Libre", "bytes"),
            Col("used_pct", "Utilisé %", "ds_pct"),
            Col("provisioned_bytes", "Provisionné", "bytes"),
            Col("provisioned_pct", "Provisionné %", "pct"),
            Col("shared", "Partagé", "bool"),
            Col("accessible", "Accessible", "bool"),
            Col("maintenance", "Maintenance", visible=False),
            Col("num_hosts", "Hôtes", "int"),
            Col("num_vms", "VM", "int"),
            Col("clusters", "Clusters"),
            Col("node", "Nœud (stockage local PVE)", visible=False),
            Col("remote", "Cible NFS / Ceph / chemin"),
            Col("array_name", "Baie"),
            Col("array_volume", "Volume / LUN sur la baie"),
            Col("extents", "Extents (naa)", visible=False),
            Col("url", "URL", visible=False),
        ],
    ),
    "switches": Grid(
        "switches", "Commutateurs virtuels", VSwitch,
        [Col("name", "Switch", frozen=True, width=180)] + SRC + [
            Col("kind", "Type"),
            Col("datacenter", "Datacenter"),
            Col("host_name", "Hôte (switch local)"),
            Col("version", "Version"),
            Col("mtu", "MTU", "int"),
            Col("num_uplinks", "Uplinks", "int"),
            Col("uplinks", "Liens montants"),
            Col("num_hosts", "Hôtes", "int"),
            Col("num_portgroups", "Portgroups", "int"),
            Col("num_ports", "Ports", "int", visible=False),
            Col("discovery", "Découverte (CDP/LLDP)"),
            Col("lacp", "LACP / mode bond"),
            Col("nioc", "NIOC", "bool", visible=False),
            Col("address", "Adresse (bridge)", visible=False),
            Col("vendor", "Éditeur", visible=False),
        ],
        intro="Les vDS et vSwitch standard côté VMware ; les bridges, bonds et zones SDN côté Proxmox.",
    ),
    "portgroups": Grid(
        "portgroups", "Portgroups et VLAN", PortGroup,
        [Col("name", "Portgroup", frozen=True, width=200)] + SRC + [
            Col("switch_name", "Switch"),
            Col("kind", "Type"),
            Col("host_name", "Hôte"),
            Col("vlan", "VLAN"),
            Col("num_vms", "VM", "int"),
            Col("num_ports", "Ports", "int"),
            Col("binding", "Liaison", visible=False),
            Col("uplink", "Portgroup d'uplink", "bool", visible=False),
        ],
    ),
    "neighbors": Grid(
        "neighbors", "Interconnexions CDP / LLDP", NetworkNeighbor,
        [Col("name", "Hôte", frozen=True, width=180)] + SRC + [
            Col("cluster_name", "Cluster"),
            Col("nic", "Carte"),
            Col("mac", "MAC"),
            Col("link_speed_mb", "Vitesse (Mb/s)", "int"),
            Col("vswitch", "vSwitch / bridge"),
            Col("protocol", "Protocole"),
            Col("switch_name", "Switch physique", width=180),
            Col("switch_port", "Port"),
            Col("switch_port_descr", "Description du port", visible=False),
            Col("switch_ip", "IP du switch"),
            Col("switch_platform", "Plateforme", width=200),
            Col("switch_vlan", "VLAN natif"),
            Col("switch_mtu", "MTU switch", "int", visible=False),
            Col("driver", "Pilote", visible=False),
        ],
        default_sort="switch_name",
        intro="Une ligne par carte physique. Filtrez sur un switch pour voir tous les serveurs qui y sont branchés.",
    ),
    "vms": Grid(
        "vms", "Machines virtuelles", VirtualMachine,
        [Col("name", "VM", frozen=True, width=200)] + SRC + [
            Col("datacenter", "Datacenter", visible=False),
            Col("cluster_name", "Cluster"),
            Col("host_name", "Hôte"),
            Col("vm_id", "ID", visible=False),
            Col("kind", "Type", visible=False),
            Col("power_state", "Alimentation", "status"),
            Col("template", "Template", "bool", visible=False),
            Col("guest_os", "Système", width=200),
            Col("guest_hostname", "Nom DNS", visible=False),
            Col("ip_address", "IP"),
            Col("vcpu", "vCPU", "int"),
            Col("cores_per_socket", "Cœurs/socket", "int", visible=False),
            Col("mem_mb", "vRAM", "mb"),
            Col("provisioned_bytes", "Provisionné", "bytes"),
            Col("used_bytes", "Utilisé", "bytes"),
            Col("num_disks", "Disques", "int", visible=False),
            Col("num_nics", "Cartes", "int", visible=False),
            Col("networks", "Réseaux"),
            Col("datastores", "Datastores", visible=False),
            Col("hw_version", "Version matérielle", visible=False),
            Col("tools_status", "Tools / agent"),
            Col("cmdb_owner", "Propriétaire (CMDB)"),
            Col("cmdb_support_group", "Groupe de support (CMDB)"),
            Col("cmdb_environment", "Environnement (CMDB)"),
            Col("cmdb_application", "Application (CMDB)", visible=False),
            Col("cmdb_managed_by", "Géré par (CMDB)", visible=False),
            Col("cmdb_status", "Statut CMDB", visible=False),
            Col("cmdb_match", "Rapprochement CMDB", visible=False),
            Col("tools_version", "Version Tools", visible=False),
            Col("tags", "Tags", visible=False),
            Col("annotation", "Notes", visible=False),
            Col("uuid", "UUID d'instance", visible=False),
            Col("bios_uuid", "UUID BIOS", visible=False),
        ],
        intro="Propriétaire, groupe de support et environnement viennent de ServiceNow quand un connecteur CMDB est configuré.",
    ),
    "arrays": Grid(
        "arrays", "Baies de stockage", StorageSystem,
        [
            Col("name", "Baie", frozen=True, width=170),
            Col("kind", "Famille"),
            Col("site", "Site"),
            Col("host", "Adresse"),
            Col("vendor", "Constructeur"),
            Col("model", "Modèle"),
            Col("serial", "N° de série"),
            Col("version", "Version"),
            Col("num_nodes", "Contrôleurs / nœuds", "int"),
            Col("num_pools", "Pools", "int"),
            Col("num_volumes", "Volumes", "int"),
            Col("capacity_bytes", "Capacité utile", "bytes"),
            Col("used_bytes", "Utilisé", "bytes"),
            Col("free_bytes", "Libre", "bytes"),
            Col("used_pct", "Utilisé %", "ds_pct"),
            Col("provisioned_bytes", "Provisionné", "bytes"),
            Col("data_reduction", "Réduction de données", "float"),
            Col("last_status", "Dernière collecte", "status"),
            Col("last_success_at", "Dernier succès", "datetime"),
        ],
        platform_field=None, scope="storage",
        intro="HPE 3PAR / Primera / Alletra, Nimble, NetApp ONTAP et Pure FlashArray.",
    ),
    "pools": Grid(
        "pools", "Pools, CPG et agrégats", StoragePool,
        [
            Col("name", "Pool", frozen=True, width=170),
            Col("system__name", "Baie", width=150),
            Col("kind", "Type"),
            Col("node", "Nœud"),
            Col("raid", "RAID"),
            Col("state", "État"),
            Col("capacity_bytes", "Capacité", "bytes"),
            Col("used_bytes", "Utilisé", "bytes"),
            Col("free_bytes", "Libre", "bytes"),
            Col("used_pct", "Utilisé %", "ds_pct"),
            Col("data_reduction", "Réduction", "float"),
            Col("num_volumes", "Volumes", "int"),
        ],
        platform_field=None, scope="storage",
    ),
    "volumes": Grid(
        "volumes", "Volumes et LUN", StorageVolume,
        [
            Col("name", "Volume / LUN", frozen=True, width=200),
            Col("system__name", "Baie", width=150),
            Col("kind", "Type"),
            Col("svm", "SVM / pod / groupe"),
            Col("pool", "Pool / CPG / agrégat"),
            Col("state", "État"),
            Col("size_bytes", "Taille", "bytes"),
            Col("used_bytes", "Utilisé", "bytes"),
            Col("used_pct", "Utilisé %", "ds_pct"),
            Col("thin", "Thin", "bool"),
            Col("data_reduction", "Réduction", "float", visible=False),
            Col("hosts", "Présenté à", width=200),
            Col("datastores", "Datastores", width=220),
            Col("wwn", "WWN / NAA", visible=False),
            Col("nas_path", "Chemin NAS", visible=False),
        ],
        platform_field=None, scope="storage",
        intro="La colonne Datastores relie chaque LUN au datastore VMFS (par NAA) ou chaque volume NFS au datastore NFS (par chemin d'export).",
    ),
}

NUMERIC = {"int", "float", "bytes", "mb", "pct", "cpu_ratio", "mem_ratio", "ds_pct"}
OP_RE = re.compile(r"^\s*(>=|<=|>|<|=|!=)\s*(-?[\d.,]+)\s*([kKmMgGtT]?[oObB]?)\s*$")
UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def _num_filter(col, raw):
    """'>4', '<= 2,5', '>500G' (unités binaires pour les colonnes en octets)."""
    m = OP_RE.match(raw)
    if not m:
        return None
    op, num, unit = m.groups()
    try:
        value = float(num.replace(",", "."))
    except ValueError:
        return None
    if col.type == "bytes" and unit:
        value *= UNITS.get(unit[0].lower(), 1)
    if col.type == "mb" and unit and unit[0].lower() in UNITS:
        value = value * UNITS[unit[0].lower()] / 1024**2
    lookup = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte", "=": "exact"}.get(op)
    if op == "!=":
        return ~Q(**{col.field: value})
    return Q(**{f"{col.field}__{lookup}": value})


def _bool_filter(col, raw):
    v = raw.strip().lower()
    if v in ("oui", "yes", "true", "1", "o", "y"):
        return Q(**{col.field: True})
    if v in ("non", "no", "false", "0", "n"):
        return Q(**{col.field: False})
    return None


def _parse_indexed(params, prefix):
    """filter[0][field]=x&filter[0][value]=y → [{'field': 'x', 'value': 'y'}]"""
    items = {}
    pat = re.compile(rf"^{prefix}\[(\d+)\]\[(\w+)\]$")
    for key in params:
        m = pat.match(key)
        if m:
            items.setdefault(int(m.group(1)), {})[m.group(2)] = params.get(key)
    return [items[i] for i in sorted(items)]


# --- Champs personnalisés : colonnes dynamiques -------------------------------
def resolve_grid(key):
    """Définition de la vue + commentaire et champs personnalisés de la source (colonnes cf_<clé>)."""
    base = GRIDS.get(key)
    if base is None or base.scope != "inventory":
        return base
    is_source = base.model is Source
    prefix = "" if is_source else "source__"
    extra = [Col(f"{prefix}comment", "Commentaire" if is_source else "Commentaire source", visible=is_source, width=220)]
    annotations = {}
    for cf in CustomField.objects.all():
        name = f"cf_{cf.key}"
        annotations[name] = KeyTextTransform(cf.key, f"{prefix}custom")
        extra.append(Col(name, cf.label, "choice" if cf.kind == "choice" else "text", visible=cf.visible))
    cols = list(base.columns)
    pos = 3 if is_source else next((i + 1 for i, c in enumerate(cols) if c.field == "source__platform"), 1)
    return replace(base, columns=cols[:pos] + extra + cols[pos:], annotations=annotations)


# --- Filtres croisés entre vues -------------------------------------------------
# Pour chaque vue : quels filtres liés elle accepte, et comment ils s'appliquent.
LINK_FILTERS = {
    "sources": [],
    "clusters": ["cluster", "host", "vm"],
    "hosts": ["cluster", "host", "datastore", "vm"],
    "datastores": ["cluster", "host", "datastore", "vm"],
    "vms": ["cluster", "host", "datastore", "vm"],
    "switches": ["host"],
    "portgroups": ["host"],
    "neighbors": ["cluster", "host"],
}
LINK_LABELS = {"source": "vCenter / Proxmox", "cluster": "Clusters", "host": "Hôtes", "datastore": "Datastores", "vm": "VM"}


def getlist(params, key):
    values = params.getlist(key) if hasattr(params, "getlist") else params.get(key, [])
    if isinstance(values, str):
        values = [values]
    return [v for v in values if v not in ("", None)]


def _pairs(values):
    """'12:nom' → (12, 'nom'). Les noms sont qualifiés par leur source : deux vCenter peuvent avoir un « Cluster01 »."""
    out = []
    for v in values:
        sid, sep, name = str(v).partition(":")
        if sep and sid.isdigit() and name:
            out.append((int(sid), name))
    return out[:200]


def _or(conds):
    q = Q(pk__in=[])
    for c in conds:
        q |= c
    return q


def _link(kind, **kw):
    return Exists(InventoryLink.objects.filter(kind=kind, **kw))


def _apply_links(grid, qs, params):
    allowed = LINK_FILTERS.get(grid.key, [])
    clusters = _pairs(getlist(params, "cluster")) if "cluster" in allowed else []
    hosts = _pairs(getlist(params, "host")) if "host" in allowed else []
    dss = _pairs(getlist(params, "datastore")) if "datastore" in allowed else []
    vm = (params.get("vm") or "").strip() if "vm" in allowed else ""
    k = grid.key

    if clusters:
        if k == "clusters":
            qs = qs.filter(_or(Q(source_id=s, name=n) for s, n in clusters))
        elif k in ("hosts", "vms", "neighbors"):
            qs = qs.filter(_or(Q(source_id=s, cluster_name=n) for s, n in clusters))
        elif k == "datastores":
            qs = qs.filter(_or(
                Q(source_id=s) & _link("host_ds", source_id=s, right_name=OuterRef("name"),
                                       left_name__in=Host.objects.filter(source_id=s, cluster_name=n).values("name"))
                for s, n in clusters))
    if hosts:
        if k == "hosts":
            qs = qs.filter(_or(Q(source_id=s, name=n) for s, n in hosts))
        elif k == "neighbors":
            qs = qs.filter(_or(Q(source_id=s, name=n) for s, n in hosts))
        elif k in ("vms", "switches", "portgroups"):
            qs = qs.filter(_or(Q(source_id=s, host_name=n) for s, n in hosts))
        elif k == "clusters":
            qs = qs.filter(_or(
                Q(source_id=s, name__in=Host.objects.filter(source_id=s, name=n).values("cluster_name")) for s, n in hosts))
        elif k == "datastores":
            qs = qs.filter(_or(
                Q(source_id=s) & (Q(node="") | Q(node=n)) & _link("host_ds", source_id=s, left_name=n, right_name=OuterRef("name"))
                for s, n in hosts))
    if dss:
        if k == "datastores":
            qs = qs.filter(_or(Q(source_id=s, name=n) for s, n in dss))
        elif k == "hosts":
            qs = qs.filter(_or(
                Q(source_id=s) & _link("host_ds", source_id=s, right_name=n, left_name=OuterRef("name")) for s, n in dss))
        elif k == "vms":
            qs = qs.filter(_or(
                Q(source_id=s) & _link("vm_ds", source_id=s, right_name=n, left_moid=OuterRef("moid")) for s, n in dss))
    if vm:
        if k == "vms":
            qs = qs.filter(name__icontains=vm)
        elif k == "datastores":
            qs = qs.filter(_link("vm_ds", source_id=OuterRef("source_id"), right_name=OuterRef("name"), left_name__icontains=vm))
        elif k in ("hosts", "clusters"):
            field_name = "host_name" if k == "hosts" else "cluster_name"
            qs = qs.filter(Exists(VirtualMachine.objects.filter(
                source_id=OuterRef("source_id"), name__icontains=vm, **{field_name: OuterRef("name")})))
    return qs


def filtered_queryset(grid, params):
    qs = grid.model.objects.all()
    if grid.model in (StoragePool, StorageVolume):
        qs = qs.select_related("system")
    elif grid.model not in (Source, StorageSystem):
        qs = qs.select_related("source")
    if grid.annotations:
        qs = qs.annotate(**grid.annotations)
    platform = params.get("platform")
    if platform in ("vmware", "proxmox") and grid.platform_field:
        qs = qs.filter(**{grid.platform_field: platform})
    source_ids = [int(v) for raw in getlist(params, "source") for v in str(raw).split(",") if v.strip().isdigit()]
    if source_ids:
        if grid.model in (Source, StorageSystem):
            qs = qs.filter(pk__in=source_ids)
        elif grid.scope == "storage":
            qs = qs.filter(system_id__in=source_ids)
        else:
            qs = qs.filter(source_id__in=source_ids)
    if grid.scope == "inventory":
        for name in grid.annotations:  # champs personnalisés : cf_<clé>=valeur (répétable)
            wanted = getlist(params, name)
            if wanted:
                cond = Q(**{f"{name}__in": [w for w in wanted if w != "(vide)"]})
                if "(vide)" in wanted:
                    cond |= Q(**{f"{name}__isnull": True}) | Q(**{name: ""})
                qs = qs.filter(cond)
        qs = _apply_links(grid, qs, params)
    q = (params.get("q") or "").strip()
    if q:
        cond = Q()
        for c in grid.columns:
            if c.type in ("text", "status", "platform", "choice"):
                cond |= Q(**{f"{c.field}__icontains": q})
        qs = qs.filter(cond)
    for f in _parse_indexed(params, "filter"):
        col = grid.col(f.get("field", ""))
        value = (f.get("value") or "").strip()
        if not col or not value:
            continue
        if col.type in NUMERIC:
            cond = _num_filter(col, value)
        elif col.type == "bool":
            cond = _bool_filter(col, value)
        elif col.type == "datetime":
            cond = None
        elif value.startswith("!"):
            cond = ~Q(**{f"{col.field}__icontains": value[1:]})
        elif value.startswith("="):
            cond = Q(**{f"{col.field}__iexact": value[1:]})
        else:
            cond = Q(**{f"{col.field}__icontains": value})
        if cond is not None:
            qs = qs.filter(cond)
    order = []
    for srt in _parse_indexed(params, "sort"):
        col = grid.col(srt.get("field", ""))
        if col:
            order.append(("-" if srt.get("dir") == "desc" else "") + col.field)
    qs = qs.order_by(*(order or [grid.default_sort]), "pk")
    return qs


# --- Listes de choix des filtres (cascade : source → cluster → hôte) ---------------
def filter_options(kind, params, limit=200):
    q = (params.get("q") or "").strip()
    platform = params.get("platform")
    source_ids = [int(v) for raw in getlist(params, "source") for v in str(raw).split(",") if v.strip().isdigit()]

    def scoped(qs):
        if platform in ("vmware", "proxmox"):
            qs = qs.filter(source__platform=platform)
        if source_ids:
            qs = qs.filter(source_id__in=source_ids)
        if q:
            qs = qs.filter(name__icontains=q)
        return qs

    if kind == "source":
        qs = Source.objects.all()
        if platform in ("vmware", "proxmox"):
            qs = qs.filter(platform=platform)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(comment__icontains=q) | Q(site__icontains=q))
        return [{"value": str(s.pk), "label": s.name, "hint": s.comment[:60]} for s in qs.only("id", "name", "comment")[:limit]]
    if kind in ("cluster", "host", "datastore"):
        model = {"cluster": Cluster, "host": Host, "datastore": Datastore}[kind]
        qs = scoped(model.objects.all())
        if kind == "host":
            clusters = _pairs(getlist(params, "cluster"))
            if clusters:
                qs = qs.filter(_or(Q(source_id=s, cluster_name=n) for s, n in clusters))
        rows = qs.values_list("source_id", "name", "source__name").distinct().order_by("name", "source__name")[:limit]
        return [{"value": f"{sid}:{name}", "label": name, "hint": src} for sid, name, src in rows]
    if kind.startswith("cf_"):
        cf = CustomField.objects.filter(key=kind[3:]).first()
        if cf is None:
            return []
        values = set(cf.choice_list())
        values.update(v for v in Source.objects.values_list(KeyTextTransform(cf.key, "custom"), flat=True) if v)
        out = sorted(v for v in values if not q or q.lower() in v.lower())[:limit]
        return [{"value": v, "label": v} for v in out] + [{"value": "(vide)", "label": "(non renseigné)"}]
    return []
