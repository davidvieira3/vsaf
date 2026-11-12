from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.roles import role_required
from core.audit import audit, client_ip

from . import ldap
from .forms import LocalUserCreateForm, LoginForm, PasswordChangeOwnForm, RealmForm, RealmTestForm, UserEditForm
from .models import LdapRealm, UserProfile

MAX_FAILURES = 10
LOCK_SECONDS = 900


@login_not_required
def login_view(request):
    if request.user.is_authenticated:
        return redirect("inventory:dashboard")
    form = LoginForm(request.POST or None)
    error = None
    if request.method == "POST" and form.is_valid():
        key = f"login-fail:{client_ip(request)}:{form.cleaned_data['username'].lower()}"
        if cache.get(key, 0) >= MAX_FAILURES:
            error = "Trop d'échecs. Réessayez dans 15 minutes."
        else:
            user = authenticate(
                request,
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"],
                realm=form.cleaned_data["realm"],
            )
            if user is not None:
                cache.delete(key)
                login(request, user)
                audit(request, "login", user.get_username())
                nxt = request.GET.get("next", "")
                if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, require_https=request.is_secure()):
                    return redirect(nxt)
                return redirect("inventory:dashboard")
            cache.set(key, cache.get(key, 0) + 1, LOCK_SECONDS)
            error = getattr(request, "ldap_error", None) or "Identifiant ou mot de passe incorrect."
            audit(request, "login-failed", form.cleaned_data["username"], error)
    return render(request, "accounts/login.html", {"form": form, "error": error})


@require_POST
def logout_view(request):
    audit(request, "logout", request.user.get_username())
    logout(request)
    return redirect("accounts:login")


def password_view(request):
    profile = getattr(request.user, "profile", None)
    if profile and not profile.is_local:
        messages.info(request, "Votre mot de passe se change dans Active Directory.")
        return redirect("inventory:dashboard")
    form = PasswordChangeOwnForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not request.user.check_password(form.cleaned_data["current"]):
            form.add_error("current", "Mot de passe actuel incorrect.")
        else:
            request.user.set_password(form.cleaned_data["new"])
            request.user.save()
            update_session_auth_hash(request, request.user)
            audit(request, "password-change", request.user.get_username())
            messages.success(request, "Mot de passe modifié.")
            return redirect("inventory:dashboard")
    return render(request, "gestion/form.html", {"form": form, "title": "Changer mon mot de passe", "submit": "Changer le mot de passe"})


# --- Utilisateurs -----------------------------------------------------------
@role_required("admin")
def users_list(request):
    users = get_user_model().objects.select_related("profile", "profile__realm").order_by("username")
    return render(request, "gestion/users_list.html", {"users": users})


@role_required("admin")
def user_create(request):
    form = LocalUserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        user = get_user_model().objects.create_user(username=d["username"], email=d["email"], password=d["password"], first_name=d["display_name"][:150])
        UserProfile.objects.create(user=user, role=d["role"], display_name=d["display_name"])
        audit(request, "user-create", user.username, d["role"])
        messages.success(request, f"Compte local {user.username} créé.")
        return redirect("accounts:users")
    return render(request, "gestion/form.html", {"form": form, "title": "Nouveau compte local", "submit": "Créer le compte", "back": "accounts:users"})


@role_required("admin")
def user_edit(request, pk):
    user = get_object_or_404(get_user_model(), pk=pk)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    form = UserEditForm(request.POST or None, initial={"role": profile.role, "is_active": user.is_active})
    if request.method == "POST" and form.is_valid():
        if user == request.user and not form.cleaned_data["is_active"]:
            messages.error(request, "Vous ne pouvez pas désactiver votre propre compte.")
        else:
            profile.role = form.cleaned_data["role"]
            profile.save()
            user.is_active = form.cleaned_data["is_active"]
            if form.cleaned_data.get("new_password") and profile.is_local:
                user.set_password(form.cleaned_data["new_password"])
            user.save()
            audit(request, "user-edit", user.username, f"role={profile.role} actif={user.is_active}")
            messages.success(request, f"Compte {user.username} mis à jour.")
            return redirect("accounts:users")
    return render(request, "gestion/form.html", {
        "form": form, "title": f"Compte {user.username}", "submit": "Enregistrer", "back": "accounts:users",
        "extra": profile.last_groups and f"Groupes AD vus à la dernière connexion :\n{profile.last_groups}",
    })


@role_required("admin")
@require_POST
def user_delete(request, pk):
    user = get_object_or_404(get_user_model(), pk=pk)
    if user == request.user:
        messages.error(request, "Vous ne pouvez pas supprimer votre propre compte.")
    else:
        audit(request, "user-delete", user.username)
        user.delete()
        messages.success(request, "Compte supprimé.")
    return redirect("accounts:users")


# --- Domaines AD ------------------------------------------------------------
@role_required("admin")
def realms_list(request):
    return render(request, "gestion/realms_list.html", {"realms": LdapRealm.objects.all()})


@role_required("admin")
def realm_edit(request, pk=None):
    realm = get_object_or_404(LdapRealm, pk=pk) if pk else None
    form = RealmForm(request.POST or None, instance=realm)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit(request, "realm-save", obj.name)
        messages.success(request, f"Domaine {obj.name} enregistré.")
        return redirect("accounts:realms")
    return render(request, "gestion/form.html", {
        "form": form, "title": realm and f"Domaine {realm.name}" or "Nouveau domaine Active Directory",
        "submit": "Enregistrer", "back": "accounts:realms",
    })


@role_required("admin")
def realm_test(request, pk):
    realm = get_object_or_404(LdapRealm, pk=pk)
    form = RealmTestForm(request.POST or None)
    steps = None
    if request.method == "POST" and form.is_valid():
        steps = ldap.test_realm(realm, form.cleaned_data.get("login") or None)
        audit(request, "realm-test", realm.name)
    return render(request, "gestion/realm_test.html", {"realm": realm, "form": form, "steps": steps})


@role_required("admin")
@require_POST
def realm_delete(request, pk):
    realm = get_object_or_404(LdapRealm, pk=pk)
    audit(request, "realm-delete", realm.name)
    realm.delete()
    messages.success(request, "Domaine supprimé.")
    return redirect("accounts:realms")
