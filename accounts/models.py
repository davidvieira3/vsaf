from django.conf import settings
from django.db import models

from core.crypto import decrypt, encrypt

ROLE_CHOICES = [("viewer", "Lecture"), ("operator", "Opérateur"), ("admin", "Administrateur")]


class LdapRealm(models.Model):
    """Un domaine Active Directory interrogé en LDAPS (un par domaine : euro, gaia, xmp...)."""

    name = models.CharField("Nom affiché", max_length=64, unique=True)
    domain = models.CharField("Domaine DNS", max_length=255, help_text="ex. euro.net.intra — sert aussi à reconnaître user@domaine")
    netbios = models.CharField("Nom NetBIOS", max_length=32, blank=True, help_text="ex. EURO — permet la saisie EURO\\jdupont")
    servers = models.CharField(
        "Contrôleurs de domaine", max_length=500, help_text="Séparés par des virgules, essayés dans l'ordre (ex. dc1.euro.net.intra,dc2.euro.net.intra)"
    )
    port = models.PositiveIntegerField(default=636, help_text="636 = LDAPS, 3269 = catalogue global LDAPS (multi-domaines)")
    use_ssl = models.BooleanField("LDAPS", default=True)
    verify_cert = models.BooleanField("Vérifier le certificat", default=True)
    ca_pem = models.TextField("CA de l'AD (PEM)", blank=True, help_text="Vide = CA globales définies dans les paramètres")
    timeout = models.PositiveIntegerField("Délai (s)", default=8)
    bind_dn = models.CharField("Compte de service (DN ou UPN)", max_length=500)
    bind_password_enc = models.TextField(blank=True)
    user_base_dn = models.CharField("Base de recherche utilisateurs", max_length=500)
    user_filter = models.CharField(
        "Filtre utilisateur",
        max_length=500,
        default="(&(objectCategory=person)(objectClass=user)(sAMAccountName={username})(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
        help_text="{username} est remplacé par l'identifiant saisi (échappé). Le dernier bloc exclut les comptes désactivés.",
    )
    group_base_dn = models.CharField("Base de recherche groupes", max_length=500, blank=True)
    nested_groups = models.BooleanField("Résoudre les groupes imbriqués", default=True)
    admin_groups = models.TextField("Groupes → Administrateur", blank=True, help_text="Un groupe par ligne : DN complet ou simple CN")
    operator_groups = models.TextField("Groupes → Opérateur", blank=True)
    viewer_groups = models.TextField("Groupes → Lecture", blank=True)
    default_role = models.CharField(
        "Rôle si aucun groupe ne correspond", max_length=16, blank=True, choices=[("", "Refuser la connexion")] + ROLE_CHOICES
    )
    priority = models.PositiveIntegerField("Ordre", default=10)
    enabled = models.BooleanField("Actif", default=True)

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self):
        return self.name

    @property
    def bind_password(self):
        return decrypt(self.bind_password_enc)

    @bind_password.setter
    def bind_password(self, value):
        self.bind_password_enc = encrypt(value)

    def server_list(self):
        return [s.strip() for s in self.servers.split(",") if s.strip()]

    @staticmethod
    def _lines(text):
        return [line.strip() for line in (text or "").splitlines() if line.strip()]

    def groups_for(self, role):
        return self._lines(getattr(self, f"{role}_groups"))


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    realm = models.ForeignKey(LdapRealm, null=True, blank=True, on_delete=models.SET_NULL, help_text="Vide = compte local")
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default="viewer")
    display_name = models.CharField(max_length=255, blank=True)
    last_groups = models.TextField(blank=True, help_text="Groupes AD vus à la dernière connexion")

    @property
    def is_local(self):
        return self.realm_id is None
