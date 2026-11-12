from django.db import models
from django.utils import timezone

from core.crypto import decrypt, encrypt

PLATFORMS = [("vmware", "VMware vCenter"), ("proxmox", "Proxmox VE")]


class Source(models.Model):
    """Un vCenter ou un cluster Proxmox à inventorier (compte en lecture seule)."""

    name = models.CharField("Nom", max_length=100, unique=True)
    platform = models.CharField("Plateforme", max_length=16, choices=PLATFORMS)
    host = models.CharField("Adresse (FQDN ou IP)", max_length=255)
    port = models.PositiveIntegerField(default=443, help_text="443 pour vCenter, 8006 pour Proxmox")
    username = models.CharField(
        "Compte", max_length=255, help_text="vCenter : svc-inventaire@vsphere.local — Proxmox : inventaire@pve (ou @pam)"
    )
    token_id = models.CharField(
        "Jeton API Proxmox (id)", max_length=100, blank=True, help_text="Proxmox uniquement : nom du jeton (ex. collecte). Vide = mot de passe."
    )
    secret_enc = models.TextField(blank=True)
    verify_ssl = models.BooleanField("Vérifier le certificat", default=True)
    site = models.CharField("Site / région", max_length=100, blank=True)
    description = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField("Collecte active", default=True)
    collect_interval_min = models.PositiveIntegerField("Intervalle (minutes)", default=240)
    collect_network_hints = models.BooleanField("Collecter CDP/LLDP", default=True)
    collect_vm_devices = models.BooleanField("Détail disques/cartes des VM", default=True)
    ssh_enabled = models.BooleanField(
        "Proxmox : enrichissement SSH", default=False, help_text="dmidecode (n° de série), ipmitool (BMC), lldpctl (voisins). Exécuté par l'application, pas par vous."
    )
    ssh_username = models.CharField(max_length=100, blank=True)
    ssh_port = models.PositiveIntegerField(default=22)
    ssh_secret_enc = models.TextField(blank=True)

    # état
    last_status = models.CharField(max_length=16, blank=True)
    last_error = models.TextField(blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_duration_s = models.FloatField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)

    # synthèse alimentée par la collecte (vue « vCenter »)
    product_name = models.CharField(max_length=255, blank=True)
    version = models.CharField(max_length=64, blank=True)
    build = models.CharField(max_length=64, blank=True)
    api_version = models.CharField(max_length=64, blank=True)
    instance_uuid = models.CharField(max_length=64, blank=True)
    num_datacenters = models.IntegerField(default=0)
    num_clusters = models.IntegerField(default=0)
    num_hosts = models.IntegerField(default=0)
    num_vms = models.IntegerField(default=0)
    num_vms_on = models.IntegerField(default=0)
    num_datastores = models.IntegerField(default=0)
    total_cores = models.IntegerField(default=0)
    total_threads = models.IntegerField(default=0)
    total_mem_bytes = models.BigIntegerField(default=0)
    vcpu_on = models.IntegerField(default=0)
    vcpu_all = models.IntegerField(default=0)
    vram_on_bytes = models.BigIntegerField(default=0)
    vram_all_bytes = models.BigIntegerField(default=0)
    cpu_ratio_on = models.FloatField(null=True, blank=True)
    cpu_ratio_all = models.FloatField(null=True, blank=True)
    mem_ratio_on = models.FloatField(null=True, blank=True)
    mem_ratio_all = models.FloatField(null=True, blank=True)
    storage_capacity_bytes = models.BigIntegerField(default=0)
    storage_free_bytes = models.BigIntegerField(default=0)
    info = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def secret(self):
        return decrypt(self.secret_enc)

    @secret.setter
    def secret(self, value):
        self.secret_enc = encrypt(value)

    @property
    def ssh_secret(self):
        return decrypt(self.ssh_secret_enc)

    @ssh_secret.setter
    def ssh_secret(self, value):
        self.ssh_secret_enc = encrypt(value)


class InventoryItem(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="%(class)s_set")
    moid = models.CharField(max_length=128, blank=True)
    name = models.CharField(max_length=255, db_index=True)
    props = models.JSONField(default=dict, blank=True)
    collected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self):
        return self.name


class RatioFields(models.Model):
    vm_count = models.IntegerField(default=0)
    vm_on = models.IntegerField(default=0)
    vcpu_on = models.IntegerField(default=0)
    vcpu_all = models.IntegerField(default=0)
    vram_on_bytes = models.BigIntegerField(default=0)
    vram_all_bytes = models.BigIntegerField(default=0)
    cpu_ratio_on = models.FloatField(null=True, blank=True)
    cpu_ratio_all = models.FloatField(null=True, blank=True)
    mem_ratio_on = models.FloatField(null=True, blank=True)
    mem_ratio_all = models.FloatField(null=True, blank=True)

    class Meta:
        abstract = True


class Datacenter(InventoryItem):
    num_clusters = models.IntegerField(default=0)
    num_hosts = models.IntegerField(default=0)
    num_vms = models.IntegerField(default=0)


class Cluster(InventoryItem, RatioFields):
    datacenter = models.CharField(max_length=255, blank=True)
    num_hosts = models.IntegerField(default=0)
    num_hosts_effective = models.IntegerField(default=0)
    total_cores = models.IntegerField(default=0)
    total_threads = models.IntegerField(default=0)
    total_mhz = models.BigIntegerField(default=0)
    total_mem_bytes = models.BigIntegerField(default=0)
    cpu_usage_pct = models.FloatField(null=True, blank=True)
    mem_usage_pct = models.FloatField(null=True, blank=True)
    ha_enabled = models.BooleanField(null=True)
    ha_admission_control = models.BooleanField(null=True)
    drs_enabled = models.BooleanField(null=True)
    drs_behavior = models.CharField(max_length=32, blank=True)
    vsan_enabled = models.BooleanField(null=True)
    evc_mode = models.CharField(max_length=64, blank=True)
    quorate = models.BooleanField(null=True)


class Host(InventoryItem, RatioFields):
    datacenter = models.CharField(max_length=255, blank=True)
    cluster_name = models.CharField(max_length=255, blank=True, db_index=True)
    mgmt_ip = models.CharField(max_length=64, blank=True)
    product = models.CharField(max_length=255, blank=True)
    version = models.CharField(max_length=64, blank=True)
    build = models.CharField(max_length=64, blank=True)
    kernel = models.CharField(max_length=128, blank=True)
    vendor = models.CharField(max_length=128, blank=True)
    model = models.CharField(max_length=128, blank=True)
    serial = models.CharField(max_length=128, blank=True, db_index=True)
    bios_version = models.CharField(max_length=64, blank=True)
    bios_date = models.CharField(max_length=32, blank=True)
    cpu_model = models.CharField(max_length=255, blank=True)
    cpu_sockets = models.IntegerField(default=0)
    cpu_cores = models.IntegerField(default=0)
    cpu_threads = models.IntegerField(default=0)
    cpu_mhz = models.IntegerField(default=0)
    mem_bytes = models.BigIntegerField(default=0)
    ht_active = models.BooleanField(null=True)
    connection_state = models.CharField(max_length=32, blank=True)
    power_state = models.CharField(max_length=32, blank=True)
    maintenance = models.BooleanField(null=True)
    lockdown = models.CharField(max_length=32, blank=True)
    boot_time = models.DateTimeField(null=True, blank=True)
    cpu_usage_pct = models.FloatField(null=True, blank=True)
    mem_usage_pct = models.FloatField(null=True, blank=True)
    num_nics = models.IntegerField(default=0)
    num_hbas = models.IntegerField(default=0)
    bmc_type = models.CharField("Type BMC", max_length=64, blank=True)
    bmc_ip = models.CharField(max_length=64, blank=True)
    bmc_name = models.CharField(max_length=255, blank=True)
    bmc_firmware = models.CharField(max_length=64, blank=True)
    bmc_health = models.CharField(max_length=32, blank=True)
    bmc_source = models.CharField(max_length=64, blank=True)


class Datastore(InventoryItem):
    datacenter = models.CharField(max_length=255, blank=True)
    ds_type = models.CharField(max_length=32, blank=True)
    capacity_bytes = models.BigIntegerField(default=0)
    free_bytes = models.BigIntegerField(default=0)
    used_bytes = models.BigIntegerField(default=0)
    provisioned_bytes = models.BigIntegerField(default=0)
    used_pct = models.FloatField(null=True, blank=True)
    provisioned_pct = models.FloatField(null=True, blank=True)
    accessible = models.BooleanField(null=True)
    maintenance = models.CharField(max_length=32, blank=True)
    shared = models.BooleanField(null=True)
    num_hosts = models.IntegerField(default=0)
    num_vms = models.IntegerField(default=0)
    fs_version = models.CharField(max_length=32, blank=True)
    remote = models.CharField(max_length=512, blank=True)
    extents = models.TextField(blank=True)
    clusters = models.TextField(blank=True)
    url = models.CharField(max_length=512, blank=True)
    node = models.CharField(max_length=255, blank=True)
    array_name = models.CharField("Baie", max_length=255, blank=True)
    array_volume = models.CharField("Volume / LUN sur la baie", max_length=500, blank=True)
    array_name = models.CharField("Baie", max_length=255, blank=True)
    array_volume = models.CharField("Volume / LUN sur la baie", max_length=500, blank=True)


class VSwitch(InventoryItem):
    datacenter = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=32)
    host_name = models.CharField(max_length=255, blank=True)
    version = models.CharField(max_length=32, blank=True)
    vendor = models.CharField(max_length=64, blank=True)
    mtu = models.IntegerField(null=True, blank=True)
    num_uplinks = models.IntegerField(default=0)
    uplinks = models.TextField(blank=True)
    num_hosts = models.IntegerField(default=0)
    num_ports = models.IntegerField(default=0)
    num_portgroups = models.IntegerField(default=0)
    discovery = models.CharField(max_length=64, blank=True)
    lacp = models.CharField(max_length=64, blank=True)
    nioc = models.BooleanField(null=True)
    address = models.CharField(max_length=128, blank=True)


class PortGroup(InventoryItem):
    switch_name = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=32)
    host_name = models.CharField(max_length=255, blank=True)
    vlan = models.CharField(max_length=255, blank=True)
    num_ports = models.IntegerField(default=0)
    binding = models.CharField(max_length=32, blank=True)
    uplink = models.BooleanField(default=False)
    num_vms = models.IntegerField(default=0)


class NetworkNeighbor(InventoryItem):
    """name = nom de l'hôte ; une ligne par carte physique."""

    cluster_name = models.CharField(max_length=255, blank=True)
    nic = models.CharField(max_length=64)
    mac = models.CharField(max_length=32, blank=True)
    link_speed_mb = models.IntegerField(null=True, blank=True)
    driver = models.CharField(max_length=64, blank=True)
    vswitch = models.CharField(max_length=255, blank=True)
    protocol = models.CharField(max_length=8, blank=True)
    switch_name = models.CharField(max_length=255, blank=True, db_index=True)
    switch_port = models.CharField(max_length=255, blank=True)
    switch_port_descr = models.CharField(max_length=255, blank=True)
    switch_ip = models.CharField(max_length=64, blank=True)
    switch_platform = models.CharField(max_length=255, blank=True)
    switch_vlan = models.CharField(max_length=32, blank=True)
    switch_mtu = models.IntegerField(null=True, blank=True)


class VirtualMachine(InventoryItem):
    datacenter = models.CharField(max_length=255, blank=True)
    cluster_name = models.CharField(max_length=255, blank=True)
    host_name = models.CharField(max_length=255, blank=True)
    vm_id = models.CharField(max_length=64, blank=True)
    kind = models.CharField(max_length=16, default="vm")
    power_state = models.CharField(max_length=32, blank=True)
    template = models.BooleanField(default=False)
    guest_os = models.CharField(max_length=255, blank=True)
    guest_hostname = models.CharField(max_length=255, blank=True)
    ip_address = models.CharField(max_length=64, blank=True)
    vcpu = models.IntegerField(default=0)
    cores_per_socket = models.IntegerField(null=True, blank=True)
    mem_mb = models.IntegerField(default=0)
    hw_version = models.CharField(max_length=32, blank=True)
    tools_status = models.CharField(max_length=64, blank=True)
    tools_version = models.CharField(max_length=64, blank=True)
    provisioned_bytes = models.BigIntegerField(default=0)
    used_bytes = models.BigIntegerField(default=0)
    num_disks = models.IntegerField(default=0)
    num_nics = models.IntegerField(default=0)
    networks = models.TextField(blank=True)
    datastores = models.TextField(blank=True)
    uuid = models.CharField(max_length=64, blank=True)
    annotation = models.TextField(blank=True)
    tags = models.CharField(max_length=500, blank=True)
    cpu_usage_mhz = models.IntegerField(null=True, blank=True)
    mem_usage_mb = models.IntegerField(null=True, blank=True)
    bios_uuid = models.CharField(max_length=64, blank=True)
    # Valeurs rapatriées de ServiceNow (recopiées après chaque collecte et chaque synchro CMDB)
    cmdb_owner = models.CharField(max_length=255, blank=True)
    cmdb_managed_by = models.CharField(max_length=255, blank=True)
    cmdb_support_group = models.CharField(max_length=255, blank=True)
    cmdb_environment = models.CharField(max_length=128, blank=True)
    cmdb_application = models.CharField(max_length=255, blank=True)
    cmdb_status = models.CharField(max_length=64, blank=True)
    cmdb_sys_id = models.CharField(max_length=32, blank=True)
    cmdb_url = models.CharField(max_length=500, blank=True)
    cmdb_match = models.CharField(max_length=64, blank=True)
    cmdb_extra = models.JSONField(default=dict, blank=True)


class CollectionRun(models.Model):
    STATUS = [("queued", "En file"), ("running", "En cours"), ("ok", "Réussie"), ("partial", "Partielle"), ("error", "Échec")]
    KIND = [("source", "Inventaire"), ("hardware", "Matériel / BMC"), ("storage", "Baie de stockage")]
    kind = models.CharField(max_length=16, choices=KIND, default="source")
    source = models.ForeignKey(Source, null=True, blank=True, on_delete=models.CASCADE, related_name="runs")
    connector = models.ForeignKey("HardwareConnector", null=True, blank=True, on_delete=models.CASCADE, related_name="runs")
    storage_system = models.ForeignKey("StorageSystem", null=True, blank=True, on_delete=models.CASCADE, related_name="runs")
    cmdb = models.ForeignKey("CmdbConnector", null=True, blank=True, on_delete=models.CASCADE, related_name="runs")
    storage = models.ForeignKey("StorageConnector", null=True, blank=True, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=16, choices=STATUS, default="queued")
    triggered_by = models.CharField(max_length=150, blank=True)
    queued_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    log = models.TextField(blank=True)

    class Meta:
        ordering = ["-queued_at"]

    @property
    def target(self):
        return self.source or self.connector or self.storage

    @property
    def duration(self):
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


class HardwareConnector(models.Model):
    KINDS = [
        ("dell_ome", "Dell OpenManage Enterprise"),
        ("hpe_oneview", "HPE OneView"),
        ("redfish", "Redfish direct (liste de BMC : iDRAC, iLO, XClarity, Supermicro...)"),
    ]
    name = models.CharField("Nom", max_length=100, unique=True)
    kind = models.CharField("Type", max_length=32, choices=KINDS)
    url = models.CharField("URL", max_length=255, blank=True, help_text="ex. https://ome.euro.net.intra — vide pour Redfish direct")
    bmc_hosts = models.TextField(
        "Liste des BMC (Redfish direct)", blank=True, help_text="Une adresse par ligne. Plages acceptées : 10.1.2.10-10.1.2.60"
    )
    username = models.CharField(max_length=255)
    secret_enc = models.TextField(blank=True)
    verify_ssl = models.BooleanField("Vérifier le certificat", default=False)
    enabled = models.BooleanField("Actif", default=True)
    last_status = models.CharField(max_length=16, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    def __str__(self):
        return self.name

    @property
    def secret(self):
        return decrypt(self.secret_enc)

    @secret.setter
    def secret(self, value):
        self.secret_enc = encrypt(value)


class HardwareRecord(models.Model):
    """Fiche matérielle remontée par OME / OneView / Redfish, rapprochée des hôtes par numéro de série."""

    connector = models.ForeignKey(HardwareConnector, on_delete=models.CASCADE, related_name="records")
    serial = models.CharField(max_length=128, db_index=True)
    alt_serial = models.CharField(max_length=128, blank=True, db_index=True)
    vendor = models.CharField(max_length=64, blank=True)
    model = models.CharField(max_length=128, blank=True)
    hostname = models.CharField(max_length=255, blank=True)
    bmc_type = models.CharField(max_length=64, blank=True)
    bmc_ip = models.CharField(max_length=64, blank=True)
    bmc_name = models.CharField(max_length=255, blank=True)
    bmc_firmware = models.CharField(max_length=64, blank=True)
    health = models.CharField(max_length=32, blank=True)
    props = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class StorageConnector(models.Model):
    """Accès en lecture seule à une baie (ou un cluster NetApp) via son API REST."""

    KINDS = [
        ("hpe_wsapi", "HPE 3PAR / Primera / Alletra 9000 / Alletra MP (WSAPI)"),
        ("hpe_nimble", "HPE Alletra 5000 / 6000 / Nimble"),
        ("netapp_ontap", "NetApp ONTAP 9.6+"),
        ("pure_flasharray", "Pure Storage FlashArray (REST 2.x)"),
    ]
    DEFAULT_PORTS = {"hpe_wsapi": 443, "hpe_nimble": 5392, "netapp_ontap": 443, "pure_flasharray": 443}

    name = models.CharField("Nom", max_length=100, unique=True)
    kind = models.CharField("Type", max_length=32, choices=KINDS)
    host = models.CharField("Adresse de gestion", max_length=255)
    port = models.PositiveIntegerField(
        null=True, blank=True, help_text="Vide = port par défaut (443 ; 5392 pour Alletra 5000/6000 ; 8080 pour un 3PAR ancien en HTTP)"
    )
    username = models.CharField("Compte", max_length=255, blank=True, help_text="Inutile pour Pure (jeton API)")
    secret_enc = models.TextField(blank=True)
    verify_ssl = models.BooleanField("Vérifier le certificat", default=False)
    site = models.CharField("Site", max_length=100, blank=True)
    enabled = models.BooleanField("Actif", default=True)
    collect_volumes = models.BooleanField("Collecter les volumes / LUN", default=True)
    collect_interval_min = models.PositiveIntegerField("Intervalle (minutes)", default=360)
    last_status = models.CharField(max_length=16, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def effective_port(self):
        return self.port or self.DEFAULT_PORTS.get(self.kind, 443)

    @property
    def base_url(self):
        return f"https://{self.host}:{self.effective_port}"

    @property
    def secret(self):
        return decrypt(self.secret_enc)

    @secret.setter
    def secret(self, value):
        self.secret_enc = encrypt(value)


class StorageArray(models.Model):
    connector = models.ForeignKey(StorageConnector, on_delete=models.CASCADE, related_name="arrays")
    name = models.CharField(max_length=255, db_index=True)
    vendor = models.CharField(max_length=64, blank=True)
    family = models.CharField(max_length=64, blank=True)
    model = models.CharField(max_length=128, blank=True)
    serial = models.CharField(max_length=128, blank=True)
    version = models.CharField(max_length=128, blank=True)
    address = models.CharField(max_length=255, blank=True)
    site = models.CharField(max_length=100, blank=True)
    num_controllers = models.IntegerField(default=0)
    num_pools = models.IntegerField(default=0)
    num_volumes = models.IntegerField(default=0)
    num_hosts = models.IntegerField(default=0)
    capacity_bytes = models.BigIntegerField(default=0)
    used_bytes = models.BigIntegerField(default=0)
    free_bytes = models.BigIntegerField(default=0)
    used_pct = models.FloatField(null=True, blank=True)
    provisioned_bytes = models.BigIntegerField(default=0)
    provisioned_pct = models.FloatField(null=True, blank=True)
    data_reduction = models.FloatField(null=True, blank=True)
    health = models.CharField(max_length=64, blank=True)
    props = models.JSONField(default=dict, blank=True)
    collected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StoragePool(models.Model):
    """CPG (3PAR/Primera/Alletra 9000), pool (Alletra 6000/Nimble), agrégat (NetApp) ; la baie entière pour Pure."""

    connector = models.ForeignKey(StorageConnector, on_delete=models.CASCADE, related_name="pools")
    array = models.ForeignKey(StorageArray, on_delete=models.CASCADE, related_name="pools")
    name = models.CharField(max_length=255, db_index=True)
    kind = models.CharField(max_length=32, blank=True)
    node = models.CharField(max_length=255, blank=True)
    raid = models.CharField(max_length=64, blank=True)
    capacity_bytes = models.BigIntegerField(default=0)
    used_bytes = models.BigIntegerField(default=0)
    free_bytes = models.BigIntegerField(default=0)
    used_pct = models.FloatField(null=True, blank=True)
    provisioned_bytes = models.BigIntegerField(default=0)
    provisioned_pct = models.FloatField(null=True, blank=True)
    data_reduction = models.FloatField(null=True, blank=True)
    num_volumes = models.IntegerField(default=0)
    state = models.CharField(max_length=64, blank=True)
    props = models.JSONField(default=dict, blank=True)
    collected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StorageVolume(models.Model):
    connector = models.ForeignKey(StorageConnector, on_delete=models.CASCADE, related_name="volumes")
    array = models.ForeignKey(StorageArray, on_delete=models.CASCADE, related_name="volumes")
    name = models.CharField(max_length=500, db_index=True)
    kind = models.CharField(max_length=32, blank=True)
    pool = models.CharField(max_length=255, blank=True)
    svm = models.CharField("SVM / domaine", max_length=255, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    used_bytes = models.BigIntegerField(default=0)
    used_pct = models.FloatField(null=True, blank=True)
    thin = models.BooleanField(null=True)
    state = models.CharField(max_length=64, blank=True)
    naa = models.CharField("Identifiant NAA", max_length=64, blank=True, db_index=True)
    serial = models.CharField(max_length=128, blank=True)
    nas_path = models.CharField("Chemin NFS", max_length=500, blank=True)
    hosts = models.TextField("Hôtes / groupes présentés", blank=True)
    datastores = models.TextField("Datastores correspondants", blank=True)
    props = models.JSONField(default=dict, blank=True)
    collected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ExportArchive(models.Model):
    """Classeur Excel horodaté conservé sur disque (historique des collectes)."""

    KINDS = [("source", "Collecte d'une source"), ("storage", "Collecte d'une baie"), ("daily", "Inventaire complet quotidien")]
    kind = models.CharField(max_length=16, choices=KINDS)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    label = models.CharField(max_length=255)
    source = models.ForeignKey(Source, null=True, blank=True, on_delete=models.SET_NULL, related_name="archives")
    storage = models.ForeignKey(StorageConnector, null=True, blank=True, on_delete=models.SET_NULL, related_name="archives")
    run = models.ForeignKey(CollectionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="archives")
    relpath = models.CharField(max_length=500, unique=True)
    size_bytes = models.BigIntegerField(default=0)
    counts = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.label


# --- Baies de stockage --------------------------------------------------------
class StorageSystem(models.Model):
    KINDS = [
        ("hpe_wsapi", "HPE 3PAR / Primera / Alletra 9000 / Alletra MP (WSAPI)"),
        ("hpe_nimble", "HPE Alletra 5000/6000 / Nimble"),
        ("netapp_ontap", "NetApp ONTAP"),
        ("pure_flasharray", "Pure Storage FlashArray"),
    ]
    name = models.CharField("Nom", max_length=100, unique=True)
    kind = models.CharField("Type", max_length=32, choices=KINDS)
    host = models.CharField("Adresse de gestion", max_length=255)
    port = models.PositiveIntegerField(
        null=True, blank=True, help_text="Vide = port par défaut (3PAR 8080, Primera/Alletra 9000 443, Nimble 5392, ONTAP 443, Pure 443)"
    )
    username = models.CharField("Compte", max_length=255, blank=True, help_text="Pure : inutile, seul le jeton API sert")
    secret_enc = models.TextField(blank=True)
    verify_ssl = models.BooleanField("Vérifier le certificat", default=False)
    site = models.CharField("Site", max_length=100, blank=True)
    enabled = models.BooleanField("Collecte active", default=True)
    collect_interval_min = models.PositiveIntegerField("Intervalle (minutes)", default=240)
    last_status = models.CharField(max_length=16, blank=True)
    last_error = models.TextField(blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    # synthèse
    vendor = models.CharField(max_length=64, blank=True)
    model = models.CharField(max_length=128, blank=True)
    serial = models.CharField(max_length=128, blank=True)
    version = models.CharField(max_length=128, blank=True)
    num_nodes = models.IntegerField(default=0)
    num_pools = models.IntegerField(default=0)
    num_volumes = models.IntegerField(default=0)
    capacity_bytes = models.BigIntegerField(default=0)
    used_bytes = models.BigIntegerField(default=0)
    free_bytes = models.BigIntegerField(default=0)
    used_pct = models.FloatField(null=True, blank=True)
    provisioned_bytes = models.BigIntegerField(default=0)
    data_reduction = models.FloatField(null=True, blank=True)
    info = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def secret(self):
        return decrypt(self.secret_enc)

    @secret.setter
    def secret(self, value):
        self.secret_enc = encrypt(value)


class StoragePool(models.Model):
    system = models.ForeignKey(StorageSystem, on_delete=models.CASCADE, related_name="pools")
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=32, blank=True)
    node = models.CharField(max_length=255, blank=True)
    raid = models.CharField(max_length=64, blank=True)
    state = models.CharField(max_length=32, blank=True)
    capacity_bytes = models.BigIntegerField(default=0)
    used_bytes = models.BigIntegerField(default=0)
    free_bytes = models.BigIntegerField(default=0)
    used_pct = models.FloatField(null=True, blank=True)
    provisioned_bytes = models.BigIntegerField(default=0)
    data_reduction = models.FloatField(null=True, blank=True)
    num_volumes = models.IntegerField(default=0)
    props = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StorageVolume(models.Model):
    system = models.ForeignKey(StorageSystem, on_delete=models.CASCADE, related_name="volumes")
    name = models.CharField(max_length=255, db_index=True)
    kind = models.CharField(max_length=32, blank=True, help_text="LUN, volume NAS, vVol...")
    pool = models.CharField(max_length=255, blank=True)
    svm = models.CharField("SVM / pod / groupe", max_length=255, blank=True)
    state = models.CharField(max_length=32, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    used_bytes = models.BigIntegerField(default=0)
    used_pct = models.FloatField(null=True, blank=True)
    thin = models.BooleanField(null=True)
    data_reduction = models.FloatField(null=True, blank=True)
    wwn = models.CharField(max_length=64, blank=True, db_index=True)
    nas_path = models.CharField(max_length=500, blank=True)
    hosts = models.TextField("Présenté à", blank=True)
    datastores = models.TextField("Datastores vSphere / PVE", blank=True)
    props = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# --- ServiceNow ---------------------------------------------------------------
DEFAULT_MATCH_RULES = "vm_instance_uuid=uuid\nbios_uuid=bios_uuid\nobject_id+vcenter_uuid=moid+vcenter_uuid\nname=name"
DEFAULT_FIELD_MAP = (
    "owner=owned_by\nmanaged_by=managed_by\nsupport_group=support_group\nenvironment=environment\n"
    "status=install_status\napplication=u_application"
)


class CmdbConnector(models.Model):
    name = models.CharField("Nom", max_length=100, unique=True)
    instance_url = models.CharField("Instance", max_length=255, help_text="ex. https://monentreprise.service-now.com")
    auth_mode = models.CharField("Authentification", max_length=16, default="basic",
                                 choices=[("basic", "Compte + mot de passe"), ("oauth", "OAuth (client credentials)")])
    username = models.CharField(max_length=255, blank=True, help_text="Compte d'intégration avec le rôle itil ou cmdb_read")
    secret_enc = models.TextField(blank=True)
    client_id = models.CharField("Client ID OAuth", max_length=255, blank=True)
    client_secret_enc = models.TextField(blank=True)
    table = models.CharField("Table", max_length=100, default="cmdb_ci_vmware_instance",
                             help_text="cmdb_ci_vmware_instance, cmdb_ci_vm_instance (toutes plateformes), ou votre classe Proxmox")
    query = models.CharField("Filtre (sysparm_query)", max_length=1000, blank=True, default="install_status!=7",
                             help_text="Syntaxe encodée ServiceNow. 7 = retiré.")
    match_rules = models.TextField(
        "Règles de rapprochement", default=DEFAULT_MATCH_RULES,
        help_text="Une règle par ligne, essayées dans l'ordre : champ_servicenow=clé_application. Clés : uuid (instance UUID), "
                  "bios_uuid, moid+vcenter_uuid, name, vm_id (Proxmox). Le rapprochement par nom n'est retenu que s'il est unique.",
    )
    field_map = models.TextField(
        "Colonnes affichées", default=DEFAULT_FIELD_MAP,
        help_text="colonne=champ_servicenow. Colonnes : owner, managed_by, support_group, environment, status, application.",
    )
    extra_fields = models.CharField("Champs supplémentaires", max_length=1000, blank=True, default="company,department,cost_center,location",
                                    help_text="Séparés par des virgules ; visibles dans le détail de la VM")
    verify_ssl = models.BooleanField("Vérifier le certificat", default=True)
    enabled = models.BooleanField("Actif", default=True)
    last_status = models.CharField(max_length=16, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    def __str__(self):
        return self.name

    @property
    def secret(self):
        return decrypt(self.secret_enc)

    @secret.setter
    def secret(self, value):
        self.secret_enc = encrypt(value)

    @property
    def client_secret(self):
        return decrypt(self.client_secret_enc)

    @client_secret.setter
    def client_secret(self, value):
        self.client_secret_enc = encrypt(value)

    @staticmethod
    def _pairs(text):
        out = []
        for line in (text or "").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                left, right = line.split("=", 1)
                out.append((left.strip(), right.strip()))
        return out

    def rules(self):
        return self._pairs(self.match_rules)

    def mapping(self):
        return self._pairs(self.field_map)

    def extras(self):
        return [f.strip() for f in self.extra_fields.split(",") if f.strip()]


class CmdbRecord(models.Model):
    connector = models.ForeignKey(CmdbConnector, on_delete=models.CASCADE, related_name="records")
    sys_id = models.CharField(max_length=32)
    name = models.CharField(max_length=255, blank=True)
    keys = models.JSONField(default=dict, blank=True)
    values = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


# --- Historique Excel -----------------------------------------------------------
class ExportFile(models.Model):
    KINDS = [("source", "Collecte d'une source"), ("storage", "Collecte d'une baie"), ("daily", "Inventaire complet quotidien"),
             ("manual", "Inventaire complet à la demande")]
    kind = models.CharField(max_length=16, choices=KINDS)
    label = models.CharField(max_length=255)
    source = models.ForeignKey(Source, null=True, blank=True, on_delete=models.SET_NULL)
    storage_system = models.ForeignKey(StorageSystem, null=True, blank=True, on_delete=models.SET_NULL)
    run = models.ForeignKey(CollectionRun, null=True, blank=True, on_delete=models.SET_NULL)
    path = models.CharField(max_length=500, unique=True)
    size_bytes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
