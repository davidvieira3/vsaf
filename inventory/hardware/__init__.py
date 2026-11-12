from . import ome, oneview, redfish

FETCHERS = {"dell_ome": ome.fetch, "hpe_oneview": oneview.fetch, "redfish": redfish.fetch}
