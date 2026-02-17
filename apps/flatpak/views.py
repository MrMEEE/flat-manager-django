from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.views import View
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from .models import GPGKey, Repository, RepositorySubset, Build
from .forms import GPGKeyGenerateForm, GPGKeyImportForm
from .utils.gpg import generate_gpg_key, import_gpg_key
from .utils.ostree import init_ostree_repo, sign_repo_summary, delete_ostree_repo

class GPGKeyListView(LoginRequiredMixin, ListView):
    """List all GPG keys."""
    model = GPGKey
    template_name = 'flatpak/gpgkey_list.html'
    context_object_name = 'gpg_keys'
    paginate_by = 20


class GPGKeyDetailView(LoginRequiredMixin, DetailView):
    """GPG key detail view."""
    model = GPGKey
    template_name = 'flatpak/gpgkey_detail.html'
    context_object_name = 'gpg_key'


@login_required
def gpgkey_generate(request):
    """Generate a new GPG key."""
    if request.method == 'POST':
        form = GPGKeyGenerateForm(request.POST)
        if form.is_valid():
            try:
                # Generate the key (no passphrase - key will be unencrypted)
                key_data = generate_gpg_key(
                    name=form.cleaned_data['name'],
                    email=form.cleaned_data['email'],
                    passphrase=None,
                    key_length=int(form.cleaned_data['key_length']),
                    comment=form.cleaned_data.get('comment', '')
                )
                
                # Create GPG key in database
                gpgkey = GPGKey.objects.create(
                    name=form.cleaned_data['name'],
                    email=form.cleaned_data['email'],
                    key_id=key_data['key_id'],
                    fingerprint=key_data['fingerprint'],
                    public_key=key_data['public_key'],
                    private_key=key_data['private_key'],
                    passphrase_hint='',
                    created_by=request.user
                )
                messages.success(request, f'GPG key "{gpgkey.name}" generated successfully.')
                return redirect('flatpak:gpgkey_detail', pk=gpgkey.pk)
            except Exception as e:
                messages.error(request, f'Failed to generate GPG key: {str(e)}')
    else:
        form = GPGKeyGenerateForm()
    
    return render(request, 'flatpak/gpgkey_generate.html', {'form': form})


@login_required
def gpgkey_import(request):
    """Import an existing GPG key."""
    if request.method == 'POST':
        form = GPGKeyImportForm(request.POST)
        if form.is_valid():
            try:
                # Validate and import the key (will decrypt if passphrase provided)
                key_info = import_gpg_key(
                    public_key=form.cleaned_data['public_key'],
                    private_key=form.cleaned_data.get('private_key'),
                    passphrase=form.cleaned_data.get('passphrase')
                )
                
                # Create GPG key in database
                gpgkey = GPGKey.objects.create(
                    name=form.cleaned_data['name'],
                    email=form.cleaned_data['email'],
                    key_id=key_info['key_id'],
                    fingerprint=key_info['fingerprint'],
                    public_key=form.cleaned_data['public_key'],
                    private_key=form.cleaned_data.get('private_key', ''),
                    passphrase_hint='',
                    created_by=request.user
                )
                messages.success(request, f'GPG key "{gpgkey.name}" imported successfully.')
                return redirect('flatpak:gpgkey_detail', pk=gpgkey.pk)
            except Exception as e:
                messages.error(request, f'Failed to import GPG key: {str(e)}')
    else:
        form = GPGKeyImportForm()
    
    return render(request, 'flatpak/gpgkey_import.html', {'form': form})


class GPGKeyCreateView(LoginRequiredMixin, CreateView):
    """Create new GPG key (legacy)."""
    model = GPGKey
    template_name = 'flatpak/gpgkey_form.html'
    fields = ['name', 'email', 'key_id', 'fingerprint', 'public_key', 'private_key', 'passphrase_hint']
    success_url = reverse_lazy('flatpak:gpgkey_list')
    
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'GPG key created successfully.')
        return super().form_valid(form)


class GPGKeyDeleteView(LoginRequiredMixin, DeleteView):
    """Delete GPG key."""
    model = GPGKey
    template_name = 'flatpak/gpgkey_confirm_delete.html'
    success_url = reverse_lazy('flatpak:gpgkey_list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(request, 'GPG key deleted successfully.')
        return super().delete(request, *args, **kwargs)


class GPGKeyDownloadView(LoginRequiredMixin, View):
    """Download public key only."""
    def get(self, request, pk):
        gpg_key = GPGKey.objects.get(pk=pk)
        response = HttpResponse(gpg_key.public_key, content_type='text/plain')
        response['Content-Disposition'] = f'attachment; filename="{gpg_key.name}_{gpg_key.key_id}_public.asc"'
        return response

class RepositoryListView(LoginRequiredMixin, ListView):
    """List all repositories."""
    model = Repository
    template_name = 'flatpak/repository_list.html'
    context_object_name = 'repositories'
    paginate_by = 20


class RepositoryDetailView(LoginRequiredMixin, DetailView):
    """Repository detail view."""
    model = Repository
    template_name = 'flatpak/repository_detail.html'
    context_object_name = 'repository'


class RepositoryCreateView(LoginRequiredMixin, CreateView):
    """Create new repository."""
    model = Repository
    template_name = 'flatpak/repository_form.html'
    fields = ['name', 'collection_id', 'description', 'gpg_key', 'parent_repos']
    success_url = reverse_lazy('flatpak:repo_list')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['gpg_keys'] = GPGKey.objects.filter(is_active=True)
        return context
    
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        
        # Initialize OSTree repository
        repo = self.object
        ostree_result = init_ostree_repo(
            repo.repo_path,
            collection_id=repo.collection_id or None,
            gpg_key=repo.gpg_key
        )
        
        if not ostree_result['success']:
            messages.warning(
                self.request,
                f"Repository created but OSTree initialization failed: {ostree_result.get('error', 'Unknown error')}"
            )
        
        # Create subsets if provided
        subset_count = 0
        while True:
            subset_name = self.request.POST.get(f'subset_name_{subset_count}')
            if not subset_name:
                break
                
            subset_collection_id = self.request.POST.get(f'subset_collection_id_{subset_count}', '')
            subset_base_url = self.request.POST.get(f'subset_base_url_{subset_count}', '')
            
            RepositorySubset.objects.create(
                repository=self.object,
                name=subset_name,
                collection_id=subset_collection_id,
                base_url=subset_base_url or None
            )
            subset_count += 1
        
        if ostree_result['success']:
            if subset_count > 0:
                messages.success(self.request, f'Repository and OSTree repo created with {subset_count} subset(s).')
            else:
                messages.success(self.request, 'Repository and OSTree repo created successfully.')
        
        return response


class RepositoryUpdateView(LoginRequiredMixin, UpdateView):
    """Update existing repository."""
    model = Repository
    template_name = 'flatpak/repository_form.html'
    fields = ['name', 'collection_id', 'description', 'gpg_key', 'parent_repos']
    
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # Exclude the current repository from parent_repos selection
        form.fields['parent_repos'].queryset = Repository.objects.exclude(pk=self.object.pk)
        return form
    
    def get_success_url(self):
        return reverse('flatpak:repo_detail', kwargs={'pk': self.object.pk})
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['gpg_keys'] = GPGKey.objects.filter(is_active=True)
        context['is_edit'] = True
        context['existing_subsets'] = self.object.subsets.all()
        return context
    
    def form_valid(self, form):
        response = super().form_valid(form)
        
        # Create new subsets if provided
        subset_count = 0
        while True:
            subset_name = self.request.POST.get(f'subset_name_{subset_count}')
            if not subset_name:
                break
                
            subset_collection_id = self.request.POST.get(f'subset_collection_id_{subset_count}', '')
            subset_base_url = self.request.POST.get(f'subset_base_url_{subset_count}', '')
            
            RepositorySubset.objects.create(
                repository=self.object,
                name=subset_name,
                collection_id=subset_collection_id,
                base_url=subset_base_url or None
            )
            subset_count += 1
        
        if subset_count > 0:
            messages.success(self.request, f'Repository updated with {subset_count} new subset(s).')
        else:
            messages.success(self.request, 'Repository updated successfully.')
        
        return response


class RepositoryDeleteView(LoginRequiredMixin, DeleteView):
    """Delete repository and its OSTree data."""
    model = Repository
    template_name = 'flatpak/repository_confirm_delete.html'
    success_url = reverse_lazy('flatpak:repo_list')
    
    def form_valid(self, form):
        """Called when the delete is confirmed."""
        repository = self.get_object()
        repo_name = repository.name
        repo_path = repository.repo_path
        
        # Delete OSTree repository from disk BEFORE deleting database record
        import os
        import logging
        logger = logging.getLogger(__name__)
        
        ostree_deleted = False
        delete_error = None
        
        logger.info(f"Attempting to delete repository: {repo_name} at path: {repo_path}")
        
        if os.path.exists(repo_path):
            logger.info(f"Repository path exists, calling delete_ostree_repo")
            result = delete_ostree_repo(repo_path)
            ostree_deleted = result['success']
            if not result['success']:
                delete_error = result.get('error', 'Unknown error')
                logger.error(f"Failed to delete OSTree repo: {delete_error}")
            else:
                logger.info(f"Successfully deleted OSTree repo at {repo_path}")
        else:
            logger.warning(f"Repository path does not exist: {repo_path}")
        
        # Show appropriate message based on deletion results
        if ostree_deleted:
            messages.success(self.request, f'Repository "{repo_name}" and its data deleted successfully.')
        elif delete_error:
            messages.warning(self.request, f'Repository "{repo_name}" deleted but failed to remove OSTree data: {delete_error}')
        else:
            messages.success(self.request, f'Repository "{repo_name}" deleted successfully.')
        
        # Delete the database record
        return super().form_valid(form)


class RepositorySubsetCreateView(LoginRequiredMixin, CreateView):
    """Create new subset for a repository."""
    model = RepositorySubset
    template_name = 'flatpak/subset_form.html'
    fields = ['name', 'collection_id', 'base_url']
    
    def dispatch(self, request, *args, **kwargs):
        self.repository = get_object_or_404(Repository, pk=kwargs['repo_pk'])
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['repository'] = self.repository
        return context
    
    def form_valid(self, form):
        form.instance.repository = self.repository
        messages.success(self.request, f'Subset "{form.instance.name}" created successfully.')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse('flatpak:repo_detail', kwargs={'pk': self.repository.pk})


class RepositorySubsetUpdateView(LoginRequiredMixin, UpdateView):
    """Update existing subset."""
    model = RepositorySubset
    template_name = 'flatpak/subset_form.html'
    fields = ['name', 'collection_id', 'base_url']
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['repository'] = self.object.repository
        context['is_edit'] = True
        return context
    
    def form_valid(self, form):
        messages.success(self.request, f'Subset "{form.instance.name}" updated successfully.')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse('flatpak:repo_detail', kwargs={'pk': self.object.repository.pk})


class RepositorySubsetDeleteView(LoginRequiredMixin, DeleteView):
    """Delete subset."""
    model = RepositorySubset
    template_name = 'flatpak/subset_confirm_delete.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['repository'] = self.object.repository
        return context
    
    def delete(self, request, *args, **kwargs):
        subset_name = self.get_object().name
        messages.success(request, f'Subset "{subset_name}" deleted successfully.')
        return super().delete(request, *args, **kwargs)
    
    def get_success_url(self):
        return reverse('flatpak:repo_detail', kwargs={'pk': self.object.repository.pk})


class BuildListView(LoginRequiredMixin, ListView):
    """List all builds."""
    model = Build
    template_name = 'flatpak/build_list.html'
    context_object_name = 'builds'
    paginate_by = 20


class BuildDetailView(LoginRequiredMixin, DetailView):
    """Build detail view with logs."""
    model = Build
    template_name = 'flatpak/build_detail.html'
    context_object_name = 'build'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['logs'] = self.object.logs.all()
        context['artifacts'] = self.object.artifacts.all()
        return context


class BuildCreateView(LoginRequiredMixin, CreateView):
    """Create new build."""
    model = Build
    template_name = 'flatpak/build_form.html'
    fields = ['repository', 'app_id', 'version', 'git_repo_url', 'git_branch', 'branch', 'arch']
    
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # Filter repositories to exclude those with parent repos
        form.fields['repository'].queryset = Repository.objects.filter(
            parent_repos__isnull=True
        )
        form.fields['repository'].help_text = "Only repositories without parent repos can have builds"
        return form
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['repositories'] = Repository.objects.filter(parent_repos__isnull=True)
        return context
    
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        
        # Build will be automatically picked up by the periodic check_pending_builds task
        if form.instance.git_repo_url:
            messages.success(
                self.request,
                f'Build {form.instance.build_id} created.'
            )
        else:
            messages.success(
                self.request,
                f'Build {form.instance.build_id} created. Ready for package upload.'
            )
        
        return response
    
    def get_success_url(self):
        return reverse('flatpak:build_detail', kwargs={'pk': self.object.pk})


class BuildUpdateView(LoginRequiredMixin, UpdateView):
    """Edit build details."""
    model = Build
    template_name = 'flatpak/build_form.html'
    fields = ['repository', 'app_id', 'version', 'branch', 'arch', 'git_repo_url', 'git_branch']
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['edit_mode'] = True
        context['build'] = self.object
        return context
    
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        return form
    
    def form_valid(self, form):
        build = self.get_object()
        
        messages.success(
            self.request,
            f'Build {build.build_id} updated successfully.'
        )
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse('flatpak:build_detail', kwargs={'pk': self.object.pk})


class BuildDeleteView(LoginRequiredMixin, DeleteView):
    """Cancel/delete build."""
    model = Build
    template_name = 'flatpak/build_confirm_delete.html'
    success_url = reverse_lazy('flatpak:build_list')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        build = self.get_object()
        context['can_cancel'] = build.status in ['pending', 'building']
        context['can_delete'] = build.status in ['failed', 'cancelled', 'published']
        return context
    
    def delete(self, request, *args, **kwargs):
        build = self.get_object()
        build_id = build.build_id
        
        # If build is in progress, just cancel it
        if build.status in ['pending', 'building', 'committing', 'publishing']:
            build.status = 'cancelled'
            build.save()
            messages.success(request, f'Build {build_id} cancelled successfully.')
            return HttpResponseRedirect(self.success_url)
        
        # Otherwise, actually delete it
        messages.success(request, f'Build {build_id} deleted successfully.')
        # TODO: Clean up build artifacts from build-repo
        return super().delete(request, *args, **kwargs)


class BuildRetryView(LoginRequiredMixin, View):
    """Retry a failed or cancelled build."""
    
    def post(self, request, pk):
        build = get_object_or_404(Build, pk=pk)
        
        # Only allow retry for failed or cancelled builds
        if build.status not in ['failed', 'cancelled', 'built', 'committed', 'published']:
            messages.error(
                request,
                f'Build {build.build_id} cannot be retried in {build.status} status.'
            )
            return redirect('flatpak:build_detail', pk=pk)
        
        # Increment build number
        build.build_number += 1
        
        # Reset build to pending state
        build.status = 'pending'
        build.started_at = None
        build.completed_at = None
        build.error_message = ''
        build.save()
        
        # Clear old logs from previous attempt
        build.logs.all().delete()
        
        messages.success(
            request,
            f'Build {build.build_id} (attempt #{build.build_number}) has been reset and will be retried shortly.'
        )
        
        return redirect('flatpak:build_detail', pk=pk)
