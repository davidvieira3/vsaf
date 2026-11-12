from django.db import models


class AppSettings(models.Model):
    """Paramètres globaux éditables depuis l'interface (singleton pk=1)."""

    cpu_ratio_warn = models.FloatField("Ratio vCPU/cœur : seuil d'alerte", default=4.0)
    cpu_ratio_crit = models.FloatField("Ratio vCPU/cœur : seuil critique", default=6.0)
    mem_ratio_warn = models.FloatField("Ratio vRAM/RAM : seuil d'alerte", default=1.0)
    mem_ratio_crit = models.FloatField("Ratio vRAM/RAM : seuil critique", default=1.25)
    ds_used_warn = models.FloatField("Stockage utilisé % : alerte", default=80)
    ds_used_crit = models.FloatField("Stockage utilisé % : critique", default=90)
    ca_bundle = models.TextField(
        "Autorités de certification internes (PEM)",
        blank=True,
        help_text="Collez ici les CA internes. Elles s'ajoutent aux CA publiques pour vérifier vCenter, Proxmox, OME, iLO...",
    )
    run_retention_days = models.PositiveIntegerField("Conservation de l'historique des collectes (jours)", default=30)
    default_collect_interval_min = models.PositiveIntegerField("Intervalle de collecte par défaut (minutes)", default=240)
    history_retention_days = models.PositiveIntegerField("Conservation des fichiers Excel (jours)", default=183)
    history_per_collection = models.BooleanField("Un fichier Excel à chaque collecte réussie", default=True)
    history_daily = models.BooleanField("Un classeur complet chaque nuit", default=True)
    archive_mode = models.CharField(
        "Classeur Excel après chaque collecte",
        max_length=16,
        default="all",
        choices=[("all", "À chaque collecte réussie"), ("daily", "Une fois par jour et par source"), ("none", "Jamais")],
    )
    archive_daily = models.BooleanField("Classeur complet chaque nuit (toutes sources et baies)", default=True)
    archive_retention_days = models.PositiveIntegerField("Conservation des classeurs Excel (jours)", default=183)

    class Meta:
        verbose_name = "paramètres"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AuditLog(models.Model):
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=255, blank=True)
    detail = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-at"]
