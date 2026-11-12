from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from .models import ROLE_CHOICES, LdapRealm


class LoginForm(forms.Form):
    username = forms.CharField(label="Identifiant", max_length=255, widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}))
    password = forms.CharField(label="Mot de passe", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    realm = forms.ChoiceField(label="Annuaire", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("auto", "Détection automatique"), ("local", "Compte local")]
        choices += [(str(r.pk), r.name) for r in LdapRealm.objects.filter(enabled=True)]
        self.fields["realm"].choices = choices


class RealmForm(forms.ModelForm):
    bind_password = forms.CharField(
        label="Mot de passe du compte de service", required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Laisser vide pour conserver le mot de passe actuel",
    )

    class Meta:
        model = LdapRealm
        exclude = ["bind_password_enc"]
        widgets = {
            "ca_pem": forms.Textarea(attrs={"rows": 4, "class": "mono"}),
            "admin_groups": forms.Textarea(attrs={"rows": 3}),
            "operator_groups": forms.Textarea(attrs={"rows": 3}),
            "viewer_groups": forms.Textarea(attrs={"rows": 3}),
        }

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("bind_password"):
            obj.bind_password = self.cleaned_data["bind_password"]
        if commit:
            obj.save()
        return obj


class RealmTestForm(forms.Form):
    login = forms.CharField(label="Identifiant à tester (optionnel)", required=False)


class LocalUserCreateForm(forms.Form):
    username = forms.CharField(label="Identifiant", max_length=150)
    display_name = forms.CharField(label="Nom affiché", required=False)
    email = forms.EmailField(required=False)
    role = forms.ChoiceField(label="Rôle", choices=ROLE_CHOICES)
    password = forms.CharField(label="Mot de passe", widget=forms.PasswordInput)

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        if "@" in u or "\\" in u:
            raise forms.ValidationError("Un compte local ne contient ni @ ni \\ (réservés aux comptes AD).")
        if get_user_model().objects.filter(username__iexact=u).exists():
            raise forms.ValidationError("Cet identifiant existe déjà.")
        return u

    def clean_password(self):
        pwd = self.cleaned_data["password"]
        validate_password(pwd)
        return pwd


class UserEditForm(forms.Form):
    role = forms.ChoiceField(label="Rôle", choices=ROLE_CHOICES, help_text="Pour un compte AD, le rôle est recalculé à chaque connexion d'après les groupes.")
    is_active = forms.BooleanField(label="Actif", required=False)
    new_password = forms.CharField(label="Nouveau mot de passe (comptes locaux)", required=False, widget=forms.PasswordInput)

    def clean_new_password(self):
        pwd = self.cleaned_data.get("new_password")
        if pwd:
            validate_password(pwd)
        return pwd


class PasswordChangeOwnForm(forms.Form):
    current = forms.CharField(label="Mot de passe actuel", widget=forms.PasswordInput)
    new = forms.CharField(label="Nouveau mot de passe", widget=forms.PasswordInput)
    confirm = forms.CharField(label="Confirmation", widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        if data.get("new") and data.get("new") != data.get("confirm"):
            raise forms.ValidationError("La confirmation ne correspond pas.")
        if data.get("new"):
            validate_password(data["new"])
        return data
