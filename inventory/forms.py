import csv
import io

from django import forms

from core.models import AppSettings

from .models import PLATFORMS, CmdbConnector, HardwareConnector, Source, StorageSystem

SECRET_HELP = "Laisser vide pour conserver la valeur enregistrée"


class SourceForm(forms.ModelForm):
    secret = forms.CharField(label="Mot de passe ou secret du jeton", required=False, widget=forms.PasswordInput(render_value=False), help_text=SECRET_HELP)
    ssh_secret = forms.CharField(
        label="SSH : mot de passe ou clé privée", required=False, widget=forms.Textarea(attrs={"rows": 3, "class": "mono", "autocomplete": "off"}),
        help_text=SECRET_HELP + ". Une clé commence par -----BEGIN.",
    )

    class Meta:
        model = Source
        fields = [
            "name", "platform", "host", "port", "username", "token_id", "verify_ssl", "site", "description",
            "enabled", "collect_interval_min", "collect_network_hints", "collect_vm_devices",
            "ssh_enabled", "ssh_username", "ssh_port",
        ]

    def clean(self):
        data = super().clean()
        if not self.instance.pk and not data.get("secret"):
            self.add_error("secret", "Obligatoire pour une nouvelle source.")
        if data.get("platform") == "vmware" and data.get("token_id"):
            self.add_error("token_id", "Les jetons API ne concernent que Proxmox.")
        if data.get("ssh_enabled") and data.get("platform") != "proxmox":
            self.add_error("ssh_enabled", "L'enrichissement SSH n'est prévu que pour Proxmox.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("secret"):
            obj.secret = self.cleaned_data["secret"]
        if self.cleaned_data.get("ssh_secret"):
            obj.ssh_secret = self.cleaned_data["ssh_secret"]
        if commit:
            obj.save()
        return obj


class SourceImportForm(forms.Form):
    data = forms.CharField(
        label="Sources à importer (CSV, séparateur ;)",
        widget=forms.Textarea(attrs={"rows": 12, "class": "mono", "placeholder": "nom;plateforme;adresse;port;compte;mot_de_passe;site;jeton_id\nvc-par-01;vmware;vc-par-01.euro.net.intra;443;svc-inv@vsphere.local;********;Paris;\npve-lyo;proxmox;pve-lyo.gaia.net.intra;8006;inventaire@pve;xxxxxxxx-xxxx;Lyon;collecte"}),
        help_text="Colonnes : nom ; plateforme (vmware|proxmox) ; adresse ; port ; compte ; mot de passe ou secret ; site ; id du jeton (Proxmox, facultatif). Une source existante (même nom) est mise à jour.",
    )
    verify_ssl = forms.BooleanField(label="Vérifier les certificats", required=False, initial=True)

    def parsed(self):
        rows, errors = [], []
        platforms = {p for p, _ in PLATFORMS}
        reader = csv.reader(io.StringIO(self.cleaned_data["data"].strip()), delimiter=";")
        for n, row in enumerate(reader, start=1):
            row = [c.strip() for c in row]
            if not row or not row[0] or row[0].lower() in ("nom", "name"):
                continue
            row += [""] * (8 - len(row))
            name, platform, host, port, user, secret, site, token = row[:8]
            if platform not in platforms:
                errors.append(f"Ligne {n} : plateforme « {platform} » inconnue")
                continue
            if not (host and user and secret):
                errors.append(f"Ligne {n} : adresse, compte et mot de passe sont obligatoires")
                continue
            rows.append({"name": name, "platform": platform, "host": host,
                         "port": int(port) if port.isdigit() else (8006 if platform == "proxmox" else 443),
                         "username": user, "secret": secret, "site": site, "token_id": token})
        return rows, errors


class ConnectorForm(forms.ModelForm):
    secret = forms.CharField(label="Mot de passe", required=False, widget=forms.PasswordInput(render_value=False), help_text=SECRET_HELP)

    class Meta:
        model = HardwareConnector
        fields = ["name", "kind", "url", "bmc_hosts", "username", "verify_ssl", "enabled"]
        widgets = {"bmc_hosts": forms.Textarea(attrs={"rows": 5, "class": "mono"})}

    def clean(self):
        data = super().clean()
        if data.get("kind") in ("dell_ome", "hpe_oneview") and not data.get("url"):
            self.add_error("url", "URL obligatoire pour ce type de connecteur.")
        if data.get("kind") == "redfish" and not data.get("bmc_hosts"):
            self.add_error("bmc_hosts", "Indiquez au moins une adresse de BMC.")
        if not self.instance.pk and not data.get("secret"):
            self.add_error("secret", "Obligatoire pour un nouveau connecteur.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("secret"):
            obj.secret = self.cleaned_data["secret"]
        if commit:
            obj.save()
        return obj


class SettingsForm(forms.ModelForm):
    class Meta:
        model = AppSettings
        fields = "__all__"
        widgets = {"ca_bundle": forms.Textarea(attrs={"rows": 8, "class": "mono"})}

    def clean_ca_bundle(self):
        pem = self.cleaned_data.get("ca_bundle", "").strip()
        if pem:
            import ssl
            try:
                ssl.create_default_context(cadata=pem)
            except ssl.SSLError as exc:
                raise forms.ValidationError(f"PEM invalide : {exc}")
        return pem


class StorageSystemForm(forms.ModelForm):
    secret = forms.CharField(label="Mot de passe (ou jeton API pour Pure)", required=False,
                             widget=forms.PasswordInput(render_value=False), help_text=SECRET_HELP)

    class Meta:
        model = StorageSystem
        fields = ["name", "kind", "host", "port", "username", "verify_ssl", "site", "enabled", "collect_interval_min"]

    def clean(self):
        data = super().clean()
        if not self.instance.pk and not data.get("secret"):
            self.add_error("secret", "Obligatoire pour une nouvelle baie.")
        if data.get("kind") != "pure_flasharray" and not data.get("username"):
            self.add_error("username", "Compte obligatoire pour ce type de baie.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("secret"):
            obj.secret = self.cleaned_data["secret"]
        if commit:
            obj.save()
        return obj


class CmdbConnectorForm(forms.ModelForm):
    secret = forms.CharField(label="Mot de passe", required=False, widget=forms.PasswordInput(render_value=False), help_text=SECRET_HELP)
    client_secret = forms.CharField(label="Secret client OAuth", required=False, widget=forms.PasswordInput(render_value=False), help_text=SECRET_HELP)

    class Meta:
        model = CmdbConnector
        fields = ["name", "instance_url", "auth_mode", "username", "client_id", "table", "query", "match_rules", "field_map",
                  "extra_fields", "verify_ssl", "enabled"]
        widgets = {"match_rules": forms.Textarea(attrs={"rows": 5, "class": "mono"}),
                   "field_map": forms.Textarea(attrs={"rows": 6, "class": "mono"})}

    def clean_instance_url(self):
        url = self.cleaned_data["instance_url"].strip().rstrip("/")
        if not url.startswith("https://"):
            raise forms.ValidationError("L'URL doit commencer par https://")
        return url

    def clean(self):
        data = super().clean()
        new = not self.instance.pk
        if data.get("auth_mode") == "basic":
            if not data.get("username"):
                self.add_error("username", "Compte obligatoire en authentification simple.")
            if new and not data.get("secret"):
                self.add_error("secret", "Obligatoire.")
        if data.get("auth_mode") == "oauth":
            if not data.get("client_id"):
                self.add_error("client_id", "Obligatoire en OAuth.")
            if new and not data.get("client_secret"):
                self.add_error("client_secret", "Obligatoire en OAuth.")
        from .cmdb import TYPED_COLUMNS
        for line in (data.get("field_map") or "").splitlines():
            if "=" in line and line.split("=", 1)[0].strip() not in TYPED_COLUMNS:
                self.add_error("field_map", f"Colonne inconnue : {line.split('=', 1)[0].strip()}")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("secret"):
            obj.secret = self.cleaned_data["secret"]
        if self.cleaned_data.get("client_secret"):
            obj.client_secret = self.cleaned_data["client_secret"]
        if commit:
            obj.save()
        return obj
