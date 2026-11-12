from django.urls import include, path

urlpatterns = [
    path("comptes/", include("accounts.urls")),
    path("gestion/", include("inventory.gestion_urls")),
    path("", include("inventory.urls")),
]
