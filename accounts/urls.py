from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("connexion/", views.login_view, name="login"),
    path("deconnexion/", views.logout_view, name="logout"),
    path("mot-de-passe/", views.password_view, name="password"),
    path("utilisateurs/", views.users_list, name="users"),
    path("utilisateurs/nouveau/", views.user_create, name="user_create"),
    path("utilisateurs/<int:pk>/", views.user_edit, name="user_edit"),
    path("utilisateurs/<int:pk>/supprimer/", views.user_delete, name="user_delete"),
    path("domaines/", views.realms_list, name="realms"),
    path("domaines/nouveau/", views.realm_edit, name="realm_create"),
    path("domaines/<int:pk>/", views.realm_edit, name="realm_edit"),
    path("domaines/<int:pk>/tester/", views.realm_test, name="realm_test"),
    path("domaines/<int:pk>/supprimer/", views.realm_delete, name="realm_delete"),
]
