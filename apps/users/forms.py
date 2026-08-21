from django import forms
from django.contrib.auth.password_validation import validate_password
from .models import (
    User,
    LDAPSource,
    LDAPGroupMapping,
    PermissionGrant,
    PermissionGroup,
    PermissionGroupPermission,
    get_action_choices_for_resource,
)


class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    password2 = forms.CharField(
        label='Confirm password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'is_local', 'is_staff']

    def clean_password1(self):
        password1 = self.cleaned_data.get('password1')
        if password1:
            validate_password(password1)
        return password1

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password1')
        p2 = cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Passwords do not match.')
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'is_local', 'is_staff', 'is_active']



class SetPasswordForm(forms.Form):
    password1 = forms.CharField(
        label='New password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    password2 = forms.CharField(
        label='Confirm new password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    def clean_password1(self):
        password1 = self.cleaned_data.get('password1')
        if password1:
            validate_password(password1)
        return password1

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password1')
        p2 = cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Passwords do not match.')
        return cleaned_data


class ChangePasswordForm(forms.Form):
    """Password-change form for the profile page — requires current password."""

    current_password = forms.CharField(
        label='Current password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )
    new_password1 = forms.CharField(
        label='New password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    new_password2 = forms.CharField(
        label='Confirm new password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current = self.cleaned_data.get('current_password')
        if current and not self.user.check_password(current):
            raise forms.ValidationError('Current password is incorrect.')
        return current

    def clean_new_password1(self):
        password = self.cleaned_data.get('new_password1')
        if password:
            validate_password(password, self.user)
        return password

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('new_password1')
        p2 = cleaned_data.get('new_password2')
        if p1 and p2 and p1 != p2:
            self.add_error('new_password2', 'Passwords do not match.')
        return cleaned_data

    def save(self):
        self.user.set_password(self.cleaned_data['new_password1'])
        self.user.save(update_fields=['password'])


# ---------------------------------------------------------------------------
# LDAPSource form
# ---------------------------------------------------------------------------

class LDAPSourceForm(forms.ModelForm):
    """
    The bind_password field is write-only: it is never pre-populated (the
    existing encrypted value is preserved if the field is left blank).
    """
    bind_password = forms.CharField(
        label='Bind password',
        required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Leave blank to keep the existing password.',
    )

    class Meta:
        model = LDAPSource
        fields = [
            'name', 'hostname', 'port', 'protocol', 'verify_certs', 'server_type',
            'bind_dn', 'bind_password',
            'base_dn', 'group_base_dn', 'group_membership', 'ldap_filter',
            'attr_username', 'attr_first_name', 'attr_last_name', 'attr_email',
            'is_active',
        ]

    def save(self, commit=True):
        # bind_password_encrypted is handled by the view — don't touch it here
        instance = super().save(commit=False)
        if commit:
            instance.save()
        return instance


# ---------------------------------------------------------------------------
# LDAPGroupMapping form
# ---------------------------------------------------------------------------

class LDAPGroupMappingForm(forms.ModelForm):
    class Meta:
        model = LDAPGroupMapping
        fields = ['ldap_group_dn', 'role', 'organisation']
        widgets = {
            'ldap_group_dn': forms.TextInput(attrs={'placeholder': 'CN=flatpak-admins,DC=example,DC=com'}),
        }


# ---------------------------------------------------------------------------
# PermissionGrant form
# ---------------------------------------------------------------------------

class PermissionGrantForm(forms.ModelForm):
    class Meta:
        model = PermissionGrant
        fields = ['resource', 'action', 'organisation', 'granted']
        widgets = {
            'resource': forms.Select(attrs={'class': 'form-select'}),
            'action': forms.Select(attrs={'class': 'form-select'}),
            'organisation': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['organisation'].required = False
        self.fields['granted'].initial = True
        self._update_action_choices_for_current_resource()

    def _update_action_choices_for_current_resource(self):
        resource = self.data.get('resource') if self.data else None
        if not resource and self.initial.get('resource'):
            resource = self.initial['resource']
        self.fields['action'].choices = get_action_choices_for_resource(resource)


class PermissionGroupForm(forms.ModelForm):
    class Meta:
        model = PermissionGroup
        fields = ['name', 'description', 'organisation']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'organisation': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['organisation'].required = False


class PermissionGroupPermissionForm(forms.ModelForm):
    class Meta:
        model = PermissionGroupPermission
        fields = ['resource', 'action', 'organisation', 'granted']
        widgets = {
            'resource': forms.Select(attrs={'class': 'form-select'}),
            'action': forms.Select(attrs={'class': 'form-select'}),
            'organisation': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['organisation'].required = False
        self.fields['granted'].initial = True
        self._update_action_choices_for_current_resource()

    def _update_action_choices_for_current_resource(self):
        resource = self.data.get('resource') if self.data else None
        if not resource and self.initial.get('resource'):
            resource = self.initial['resource']
        self.fields['action'].choices = get_action_choices_for_resource(resource)

