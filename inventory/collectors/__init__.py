def collector_for(source, log):
    if source.platform == "vmware":
        from .vsphere import VSphereCollector
        return VSphereCollector(source, log)
    if source.platform == "proxmox":
        from .proxmox import ProxmoxCollector
        return ProxmoxCollector(source, log)
    raise ValueError(f"Plateforme inconnue : {source.platform}")
