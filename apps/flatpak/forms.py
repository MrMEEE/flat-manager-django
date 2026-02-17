"""
Flatpak app forms.
"""
from django import forms
from .models import GPGKey, Repository, Build, Token


class GPGKeyGenerateForm(forms.Form):
    """Form for generating a new GPG key."""
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Flatpak Builder'})
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'e.g., builder@example.com'})
    )
    comment = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional comment'})
    )
    passphrase = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Leave empty for no passphrase'})
    )
    passphrase_confirm = forms.CharField(
        required=False,
        label='Confirm Passphrase',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm passphrase'})
    )
    passphrase_hint = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Hint for remembering passphrase'})
    )
    key_length = forms.ChoiceField(
        choices=[(2048, '2048 bits'), (4096, '4096 bits (recommended)')],
        initial=4096,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    def clean(self):
        cleaned_data = super().clean()
        passphrase = cleaned_data.get('passphrase')
        passphrase_confirm = cleaned_data.get('passphrase_confirm')
        
        if passphrase and passphrase != passphrase_confirm:
            raise forms.ValidationError("Passphrases do not match")
        
        return cleaned_data


class GPGKeyImportForm(forms.Form):
    """Form for importing an existing GPG key."""
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Name for this key'})
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email address'})
    )
    public_key = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 10,
            'placeholder': '-----BEGIN PGP PUBLIC KEY BLOCK-----\n...\n-----END PGP PUBLIC KEY BLOCK-----'
        }),
        help_text='ASCII armored public key'
    )
    private_key = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 10,
            'placeholder': '-----BEGIN PGP PRIVATE KEY BLOCK-----\n...\n-----END PGP PRIVATE KEY BLOCK-----'
        }),
        help_text='ASCII armored private key'
    )
    passphrase_hint = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Hint for passphrase (if any)'})
    )
    
    def clean(self):
        cleaned_data = super().clean()
        public_key = cleaned_data.get('public_key')
        
        if public_key and not public_key.strip().startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----'):
            raise forms.ValidationError("Invalid public key format")
        
        private_key = cleaned_data.get('private_key')
        if private_key and not private_key.strip().startswith('-----BEGIN PGP PRIVATE KEY BLOCK-----'):
            raise forms.ValidationError("Invalid private key format")
        
        return cleaned_data
