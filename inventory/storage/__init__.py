"""Connecteurs baies de stockage. Chaque module expose fetch(system, log) -> (synthese, pools, volumes)."""


def fetcher_for(kind):
    if kind == "hpe_wsapi":
        from .hpe_wsapi import fetch
    elif kind == "hpe_nimble":
        from .hpe_nimble import fetch
    elif kind == "netapp_ontap":
        from .netapp import fetch
    elif kind == "pure_flasharray":
        from .pure import fetch
    else:
        raise ValueError(f"Type de baie inconnu : {kind}")
    return fetch
