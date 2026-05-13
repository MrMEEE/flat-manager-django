from django import forms
from django.contrib.auth.password_validation import validate_password
from .models import User, LDAPSource, LDAPGroupMapping, UserRole, ROLE_CHOICES


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
# UserRole form
# ---------------------------------------------------------------------------

class UserRoleForm(forms.ModelForm):
    class Meta:
        model = UserRole
        fields = ['role', 'organisation']

