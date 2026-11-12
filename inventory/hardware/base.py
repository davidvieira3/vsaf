import ipaddress

import requests
import urllib3

from core.tls import requests_verify

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEALTH = {1000: "OK", 2000: "Inconnu", 3000: "Alerte", 4000: "Critique", 5000: "Inconnu"}


def session(connector):
    sess = requests.Session()
    sess.verify = requests_verify(connector.verify_ssl)
    sess.headers.update({"Accept": "application/json"})
    return sess


def expand_hosts(text):
    """Accepte une adresse par ligne, et des plages 10.0.0.10-10.0.0.40."""
    out = []
    for line in (text or "").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        if "-" in line and line.count(".") >= 6:
            start, end = (ipaddress.ip_address(x.strip()) for x in line.split("-", 1))
            if int(end) - int(start) > 4096:
                raise ValueError(f"Plage trop large : {line}")
            out += [str(ipaddress.ip_address(i)) for i in range(int(start), int(end) + 1)]
        else:
            out.append(line)
    return out
