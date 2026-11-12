import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.models import UserProfile
from core.models import AppSettings


class Command(BaseCommand):
    help = "Crée le compte administrateur local initial (variables ADMIN_USERNAME / ADMIN_PASSWORD) s'il n'existe pas."

    def handle(self, *args, **opts):
        AppSettings.get()
        username = os.environ.get("ADMIN_USERNAME", "admin")
        password = os.environ.get("ADMIN_PASSWORD")
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Compte {username} déjà présent.")
            return
        if not password:
            self.stderr.write("ADMIN_PASSWORD vide : compte initial non créé.")
            return
        user = User.objects.create_superuser(username=username, email="", password=password)
        UserProfile.objects.create(user=user, role="admin", display_name="Administrateur local")
        self.stdout.write(self.style.SUCCESS(f"Compte administrateur {username} créé."))
