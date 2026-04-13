import os
import subprocess
import logging
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.views import View
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from .models import GPGKey, Repository, RepositorySubset, Package, Build, Promotion, BuildStreamSource, Client, ExternalRef, ExternalRefPromotion, Organisation
from .forms import GPGKeyGenerateForm, GPGKeyImportForm, GPGKeyRenewForm
from .utils.gpg import generate_gpg_key, import_gpg_key, renew_gpg_key
from .utils.ostree import init_ostree_repo, sign_repo_summary, delete_ostree_repo, temp_gpg_homedir, update_repo_metadata

logger = logging.getLogger(__name__)

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
                    comment=form.cleaned_data.get('comment', ''),
                    expires=form.cleaned_data.get('key_lifetime', '0'),
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
                    expires_at=key_data.get('expires_at'),
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


@login_required
def gpgkey_renew(request, pk):
    """Extend the expiry date of an existing GPG key."""
    gpg_key = get_object_or_404(GPGKey, pk=pk)

    if request.method == 'POST':
        form = GPGKeyRenewForm(request.POST)
        if form.is_valid():
            try:
                duration = form.cleaned_data['key_lifetime']
                result = renew_gpg_key(gpg_key, duration)

                # Persist updated public key and new expiry
                gpg_key.public_key = result['public_key']
                gpg_key.expires_at = result['expires_at']
                gpg_key.save(update_fields=['public_key', 'expires_at', 'updated_at'])

                # Refresh the .gpg file for every repo that uses this key
                for repo in gpg_key.repositories.all():
                    gpg_file = os.path.join(settings.REPOS_BASE_PATH,
                                            f'{repo.folder_name}.gpg')
                    try:
                        with open(gpg_file, 'w') as _f:
                            _f.write(result['public_key'])
                    except OSError as _e:
                        logger.warning('Could not update .gpg file for repo %s: %s',
                                       repo.name, _e)

                messages.success(request, f'GPG key "{gpg_key.name}" renewed successfully.')
                return redirect('flatpak:gpgkey_detail', pk=gpg_key.pk)
            except Exception as e:
                messages.error(request, f'Failed to renew GPG key: {str(e)}')
    else:
        form = GPGKeyRenewForm()

    return render(request, 'flatpak/gpgkey_renew.html', {'form': form, 'gpg_key': gpg_key})


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
        # Capture old key before saving so we can detect a change
        old_key_id = self.object.gpg_key_id
        response = super().form_valid(form)
        new_key_id = self.object.gpg_key_id

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

        # Re-sign the repository if the GPG key changed
        if old_key_id != new_key_id:
            repo = self.object
            repo_path = repo.repo_path
            if os.path.exists(repo_path):
                # Update the OSTree core.gpg-sign config entry
                if repo.gpg_key:
                    subprocess.run(
                        ['ostree', 'config', 'set', '--repo', repo_path,
                         'core.gpg-sign', repo.gpg_key.key_id],
                        capture_output=True, text=True,
                    )
                    # Refresh the public key file used by clients
                    gpg_file = os.path.join(settings.REPOS_BASE_PATH,
                                            f'{repo.folder_name}.gpg')
                    with open(gpg_file, 'w') as _f:
                        _f.write(repo.gpg_key.public_key)
                else:
                    # Key removed — clear signing config and .gpg file
                    subprocess.run(
                        ['ostree', 'config', 'delete', '--repo', repo_path,
                         'core.gpg-sign'],
                        capture_output=True, text=True,
                    )
                    gpg_file = os.path.join(settings.REPOS_BASE_PATH,
                                            f'{repo.folder_name}.gpg')
                    if os.path.exists(gpg_file):
                        os.remove(gpg_file)

                # Re-sign everything (summary, commits, deltas) with the new key
                result = update_repo_metadata(repo_path, gpg_key=repo.gpg_key,
                                              generate_deltas=True)
                if result['success']:
                    messages.success(
                        self.request,
                        'Repository re-signed with the new GPG key successfully.'
                    )
                else:
                    messages.warning(
                        self.request,
                        f'Repository updated but re-signing encountered an issue: '
                        f'{result.get("detail") or result.get("error", "unknown error")}'
                    )
            else:
                messages.info(
                    self.request,
                    'GPG key updated in database. Repository directory not found on disk '
                    '— no re-signing performed.'
                )

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


def _try_refresh_local_appstream(repo_name):
    """
    After updating server-side repo metadata, refresh the local flatpak user
    appstream cache for any remote whose URL points to this repository.

    This is a best-effort operation (silently ignored when the server is not
    also a flatpak client, or when the remote isn't configured).  Returns the
    remote name on success, None otherwise.
    """
    import configparser
    import glob

    config_path = os.path.expanduser('~/.local/share/flatpak/repo/config')
    if not os.path.exists(config_path):
        return None

    cfg = configparser.ConfigParser()
    cfg.read(config_path)

    for section in cfg.sections():
        if not section.startswith('remote "'):
            continue
        remote_name = section[8:-1]          # strip 'remote "' and trailing '"'
        url = cfg.get(section, 'url', fallback='').rstrip('/')
        if not url.endswith('/repositories/' + repo_name):
            continue

        # Clear stale summary index cache so flatpak fetches a fresh one
        cache_dir = os.path.expanduser('~/.local/share/flatpak/repo/tmp/cache/summaries')
        for f in glob.glob(os.path.join(cache_dir, remote_name + '*')):
            try:
                os.remove(f)
            except OSError:
                pass

        # Remove any commitpartial files that would block pulling
        state_dir = os.path.expanduser('~/.local/share/flatpak/repo/state')
        for f in glob.glob(os.path.join(state_dir, '*.commitpartial')):
            try:
                os.remove(f)
            except OSError:
                pass

        r = subprocess.run(
            ['flatpak', '--user', 'update', '--appstream', remote_name],
            capture_output=True, text=True, timeout=120,
        )
        return remote_name if r.returncode == 0 else None

    return None


class RepositoryUpdateMetadataView(LoginRequiredMixin, View):
    """
    Re-run flatpak build-update-repo to regenerate appstream metadata, sign
    everything correctly, then refresh the local flatpak appstream cache so
    that ``flatpak remote-ls`` immediately shows the correct version.
    """

    def post(self, request, pk):
        from django.http import JsonResponse

        repository = get_object_or_404(Repository, pk=pk)
        repo_path = repository.repo_path

        if not os.path.exists(os.path.join(repo_path, 'config')):
            return JsonResponse({'error': 'Repository not found on disk'}, status=404)

        try:
            result = update_repo_metadata(repo_path, repository.gpg_key)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

        if not result['success']:
            return JsonResponse({
                'status': 'warning',
                'message': result.get('message', 'Partial update'),
                'detail': result.get('detail', result.get('error', '')),
            })

        # Best-effort: refresh local appstream cache when server == client (dev setup)
        refreshed_remote = _try_refresh_local_appstream(repository.name)
        msg = result['message']
        if refreshed_remote:
            msg += f'; appstream cache refreshed for remote "{refreshed_remote}"'

        return JsonResponse({'status': 'ok', 'message': msg})


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


class PackageListView(LoginRequiredMixin, ListView):
    """List all packages with optional filtering."""
    model = Package
    template_name = 'flatpak/package_list.html'
    context_object_name = 'packages'
    paginate_by = 20

    def get_queryset(self):
        from django.db.models import Q, F, Exists, OuterRef
        from apps.flatpak.models import BuildExternalRef
        qs = Package.objects.select_related('repository').annotate(
            has_dep_data=Exists(BuildExternalRef.objects.filter(build__package=OuterRef('pk')))
        ).order_by('-created_at')
        q = self.request.GET.get('q', '').strip()
        status = self.request.GET.get('status', '').strip()
        repo = self.request.GET.get('repo', '').strip()
        if q:
            qs = qs.filter(Q(package_name__icontains=q) | Q(package_id__icontains=q))
        if status == 'outdated':
            qs = qs.filter(
                upstream_version__isnull=False
            ).exclude(upstream_version='').exclude(upstream_version=F('version'))
        elif status == 'deps_outdated':
            qs = qs.filter(deps_need_rebuild=True)
        elif status == 'building':
            # Match all in-progress states (mirrors the dashboard counter)
            qs = qs.filter(status__in=['building', 'committing', 'committed', 'publishing'])
        elif status:
            qs = qs.filter(status=status)
        if repo:
            qs = qs.filter(repository_id=repo)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['repositories'] = Repository.objects.filter(is_active=True)
        ctx['status_choices'] = list(Package.STATUS_CHOICES) + [('outdated', 'Outdated')]
        ctx['filter_q'] = self.request.GET.get('q', '')
        ctx['filter_status'] = self.request.GET.get('status', '')
        ctx['filter_repo'] = self.request.GET.get('repo', '')
        get_params = self.request.GET.copy()
        get_params.pop('page', None)
        ctx['filter_params'] = get_params.urlencode()
        ctx['failed_count'] = Package.objects.filter(status__in=['failed', 'cancelled']).count()
        ctx['all_organisations'] = Organisation.objects.all()
        return ctx


class PackageDetailView(LoginRequiredMixin, DetailView):
    """Package detail view with build history."""
    model = Package
    template_name = 'flatpak/package_detail.html'
    context_object_name = 'package'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Get all builds (history) for this package
        context['builds'] = self.object.builds.all().order_by('-build_number')

        # Find the most recent build that has dependency snapshots and expose them
        from apps.flatpak.models import BuildExternalRef
        latest_snapshots = []
        for b in context['builds']:
            snaps = list(b.external_ref_snapshots.select_related('external_ref').all())
            if snaps:
                latest_snapshots = snaps
                break
        context['latest_dep_snapshots'] = latest_snapshots

        # Build a coverage map for produced refs
        ref_coverage = {}
        for bst in BuildStreamSource.objects.all().only('pk', 'name', 'produced_refs'):
            for raw in bst.produced_refs.splitlines():
                r = raw.strip()
                if r and r not in ref_coverage:
                    ref_coverage[r] = {'kind': 'bst', 'pk': bst.pk, 'name': bst.name}
        for pkg in Package.objects.exclude(pk=self.object.pk).only('pk', 'package_id', 'package_name', 'arch', 'branch'):
            disp = pkg.package_name or pkg.package_id
            for prefix in ('app', 'runtime', 'appstream'):
                key = f"{prefix}/{pkg.package_id}/{pkg.arch}/{pkg.branch}"
                if key not in ref_coverage:
                    ref_coverage[key] = {'kind': 'package', 'pk': pkg.pk, 'name': disp}

        produced = [r for r in self.object.produced_refs.splitlines() if r.strip()]
        grouped = {}
        for ref in sorted(produced):
            bucket = ref.split('/')[0] if '/' in ref else 'other'
            grouped.setdefault(bucket, []).append({
                'ref': ref,
                'coverage': ref_coverage.get(ref),
            })
        context['produced_refs'] = produced
        context['produced_refs_grouped'] = grouped
        return context


class PackageBuildsApiView(LoginRequiredMixin, View):
    """AJAX endpoint – returns build history for a package as JSON."""

    def get(self, request, pk):
        try:
            package = Package.objects.get(pk=pk)
        except Package.DoesNotExist:
            return JsonResponse({'error': 'Not found'}, status=404)

        builds_data = []
        for b in package.builds.all().order_by('-build_number'):
            duration = '-'
            if b.completed_at and b.started_at:
                total = int((b.completed_at - b.started_at).total_seconds())
                h, rem = divmod(total, 3600)
                m, s = divmod(rem, 60)
                parts = ([f"{h}h"] if h else []) + ([f"{m}m"] if m else []) + [f"{s}s"]
                duration = ' '.join(parts)
            builds_data.append({
                'id': b.id,
                'build_number': b.build_number,
                'status': b.status,
                'version': b.version or '',
                'started_at': b.started_at.strftime('%b %d, %H:%M') if b.started_at else '',
                'duration': duration,
            })

        return JsonResponse({
            'builds': builds_data,
            'package_status': package.status,
            'build_number': package.build_number,
        })


class PackageCheckUpstreamView(LoginRequiredMixin, View):
    """AJAX — immediately fetch the latest upstream version tag for a package."""

    def post(self, request, pk):
        from django.utils import timezone as tz
        from apps.flatpak.tasks import _fetch_latest_upstream_tag, _run_version_script, _normalise_version
        package = get_object_or_404(Package, pk=pk)
        if not package.upstream_url and not package.upstream_version_script.strip():
            return JsonResponse({'error': 'No upstream URL or version script configured for this package'}, status=400)

        version = None
        error = None

        # Step 1: custom version script
        if package.upstream_version_script.strip():
            version, error = _run_version_script(package.upstream_version_script, package.package_id)

        # Step 2: git tag fallback
        if not version and package.upstream_url:
            version, error = _fetch_latest_upstream_tag(package.upstream_url)

        if not version:
            return JsonResponse({'error': error or 'Could not determine upstream version'}, status=502)

        version = _normalise_version(version)
        package.upstream_version = version
        package.upstream_checked_at = tz.now()
        package.save(update_fields=['upstream_version', 'upstream_checked_at'])
        return JsonResponse({
            'version': version,
            'has_update': bool(package.version and version and version != package.version),
        })


class PackageCheckAvailableView(LoginRequiredMixin, View):
    """AJAX — immediately check the available (git-source) version for a package."""

    def post(self, request, pk):
        from django.utils import timezone as tz
        from apps.flatpak.tasks import _fetch_available_version
        package = get_object_or_404(Package, pk=pk)
        if not package.git_repo_url:
            return JsonResponse(
                {'error': 'No git repository URL configured for this package'},
                status=400,
            )

        version, error = _fetch_available_version(package)
        if not version:
            return JsonResponse({'error': error or 'Could not determine available version'}, status=502)

        package.available_version = version
        package.available_version_checked_at = tz.now()
        package.save(update_fields=['available_version', 'available_version_checked_at'])
        try:
            from packaging.version import Version
            has_update = bool(package.version and version and Version(version) > Version(package.version))
        except Exception:
            has_update = bool(package.version and version and version != package.version)
        return JsonResponse({
            'version': version,
            'has_update': has_update,
        })


def get_available_promotion_targets(build):
    """
    Returns list of Repository objects that this build can currently be promoted to.
    Chain enforcement: a child repo is available only when all of its parents
    (excluding the build's own source repo) already have a completed promotion
    for this build.
    """
    source_repo = build.package.repository
    completed_ids = set(
        build.promotions.filter(status='promoted').values_list('target_repo_id', flat=True)
    )
    # Repos with a pending/promoting/promoted record — don't offer these again
    taken_ids = set(
        build.promotions.exclude(status='failed').values_list('target_repo_id', flat=True)
    )
    available = []
    visited = {source_repo.id}
    # Explore children of source_repo + children of any completed-promotion repo
    to_explore = [source_repo] + list(Repository.objects.filter(id__in=completed_ids))
    for from_repo in to_explore:
        for child in from_repo.child_repos.filter(is_active=True):
            if child.id in visited:
                continue
            visited.add(child.id)
            parent_ids = set(child.parent_repos.values_list('id', flat=True)) - {source_repo.id}
            if parent_ids.issubset(completed_ids) and child.id not in taken_ids:
                available.append(child)
    return available


def get_available_bst_promotion_targets(build):
    """
    Returns list of Repository objects that this BST build can currently be promoted to.
    Same chain logic as get_available_promotion_targets but for BST builds.
    """
    source_repo = build.bst_source.repository
    completed_ids = set(
        build.bst_promotions.filter(status='promoted').values_list('target_repo_id', flat=True)
    )
    taken_ids = set(
        build.bst_promotions.exclude(status='failed').values_list('target_repo_id', flat=True)
    )
    available = []
    visited = {source_repo.id}
    to_explore = [source_repo] + list(Repository.objects.filter(id__in=completed_ids))
    for from_repo in to_explore:
        for child in from_repo.child_repos.filter(is_active=True):
            if child.id in visited:
                continue
            visited.add(child.id)
            parent_ids = set(child.parent_repos.values_list('id', flat=True)) - {source_repo.id}
            if parent_ids.issubset(completed_ids) and child.id not in taken_ids:
                available.append(child)
    return available


def get_available_external_ref_promotion_targets(external_ref):
    """
    Returns list of Repository objects that this ExternalRef can currently be
    promoted to. Same chain logic as package/BST promotions.
    """
    source_repo = external_ref.repository
    completed_ids = set(
        external_ref.promotions.filter(status='promoted').values_list('target_repo_id', flat=True)
    )
    taken_ids = set(
        external_ref.promotions.exclude(status='failed').values_list('target_repo_id', flat=True)
    )
    available = []
    visited = {source_repo.id}
    to_explore = [source_repo] + list(Repository.objects.filter(id__in=completed_ids))
    for from_repo in to_explore:
        for child in from_repo.child_repos.filter(is_active=True):
            if child.id in visited:
                continue
            visited.add(child.id)
            parent_ids = set(child.parent_repos.values_list('id', flat=True)) - {source_repo.id}
            if parent_ids.issubset(completed_ids) and child.id not in taken_ids:
                available.append(child)
    return available


class BuildListView(LoginRequiredMixin, ListView):
    """List all builds across all packages with optional filtering."""
    model = Build
    template_name = 'flatpak/build_list.html'
    context_object_name = 'builds'
    paginate_by = 50

    def get_queryset(self):
        from django.db.models import Q
        qs = Build.objects.select_related(
            'package', 'package__repository',
            'bst_source', 'bst_source__repository',
        ).order_by('-started_at')
        q = self.request.GET.get('q', '').strip()
        status = self.request.GET.get('status', '').strip()
        repo = self.request.GET.get('repo', '').strip()
        if q:
            qs = qs.filter(
                Q(package__package_name__icontains=q)
                | Q(package__package_id__icontains=q)
                | Q(bst_source__name__icontains=q)
            )
        if status:
            qs = qs.filter(status=status)
        if repo:
            qs = qs.filter(
                Q(package__repository_id=repo) | Q(bst_source__repository_id=repo)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['repositories'] = Repository.objects.filter(is_active=True)
        ctx['status_choices'] = Build.STATUS_CHOICES
        ctx['filter_q'] = self.request.GET.get('q', '')
        ctx['filter_status'] = self.request.GET.get('status', '')
        ctx['filter_repo'] = self.request.GET.get('repo', '')
        get_params = self.request.GET.copy()
        get_params.pop('page', None)
        ctx['filter_params'] = get_params.urlencode()
        return ctx


class BuildDetailView(LoginRequiredMixin, DetailView):
    """Build detail view with logs."""
    model = Build
    template_name = 'flatpak/build_detail.html'
    context_object_name = 'build'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['logs'] = self.object.logs.all().order_by('timestamp')
        context['artifacts'] = self.object.artifacts.all()
        context['promotions'] = self.object.promotions.select_related(
            'target_repo', 'promoted_by'
        ).all()
        context['available_promotion_targets'] = (
            get_available_promotion_targets(self.object)
            if self.object.status == 'published' and self.object.package_id else []
        )
        context['available_bst_promotion_targets'] = (
            get_available_bst_promotion_targets(self.object)
            if self.object.status == 'published' and self.object.bst_source_id else []
        )
        from apps.flatpak.models import BstPromotion
        context['bst_promotions'] = (
            self.object.bst_promotions.select_related('target_repo', 'promoted_by').all()
            if self.object.bst_source_id else []
        )
        return context


class BuildPromotionsApiView(LoginRequiredMixin, View):
    """AJAX — returns current promotions and available targets for a build."""

    def get(self, request, pk):
        build = get_object_or_404(Build, pk=pk)
        promotions_data = []
        for p in build.promotions.select_related('target_repo', 'promoted_by').all():
            promotions_data.append({
                'id': p.id,
                'target_repo_id': p.target_repo_id,
                'target_repo_name': p.target_repo.name,
                'status': p.status,
                'error_message': p.error_message,
                'promoted_by': p.promoted_by.username if p.promoted_by else None,
                'created_at': p.created_at.strftime('%b %d, %H:%M'),
                'completed_at': p.completed_at.strftime('%b %d, %H:%M') if p.completed_at else None,
            })
        available_data = []
        if build.status == 'published':
            for r in get_available_promotion_targets(build):
                available_data.append({'id': r.id, 'name': r.name})
        return JsonResponse({
            'promotions': promotions_data,
            'available': available_data,
            'build_status': build.status,
        })


class PromoteView(LoginRequiredMixin, View):
    """Create and queue a promotion for a published build."""

    def post(self, request, build_pk):
        import json as _json
        build = get_object_or_404(Build, pk=build_pk)
        if build.status != 'published':
            return JsonResponse({'error': 'Build must be published before promoting'}, status=400)
        try:
            data = _json.loads(request.body)
            target_repo_id = int(data.get('target_repo_id', 0))
        except Exception:
            return JsonResponse({'error': 'Invalid request body'}, status=400)
        target_repo = get_object_or_404(Repository, pk=target_repo_id)
        available_ids = [r.id for r in get_available_promotion_targets(build)]
        if target_repo_id not in available_ids:
            return JsonResponse(
                {'error': f'Cannot promote to {target_repo.name}: prerequisites not met or already promoted'},
                status=400
            )
        try:
            promotion = Promotion.objects.create(
                build=build,
                package=build.package,
                target_repo=target_repo,
                status='pending',
                promoted_by=request.user,
            )
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
        from apps.flatpak.tasks import promote_build_task
        promote_build_task.delay(promotion.id)
        return JsonResponse({'status': 'ok', 'promotion_id': promotion.id})


class PromotionRetryView(LoginRequiredMixin, View):
    """Re-queue a pending or failed promotion."""

    def post(self, request, pk):
        from apps.flatpak.tasks import promote_build_task
        promotion = get_object_or_404(Promotion, pk=pk)
        if promotion.status not in ('pending', 'failed'):
            return JsonResponse(
                {'error': f'Promotion is {promotion.status}, only pending/failed can be retried'},
                status=400
            )
        promotion.status = 'pending'
        promotion.error_message = ''
        promotion.save(update_fields=['status', 'error_message'])
        promote_build_task.delay(promotion.id)
        return JsonResponse({'status': 'ok'})


class BstPromoteView(LoginRequiredMixin, View):
    """Create and queue a BST promotion for a published BST build."""

    def post(self, request, build_pk):
        import json as _json
        from apps.flatpak.models import BstPromotion
        build = get_object_or_404(Build, pk=build_pk)
        if not build.bst_source_id:
            return JsonResponse({'error': 'Not a BST build'}, status=400)
        if build.status != 'published':
            return JsonResponse({'error': 'Build must be published before promoting'}, status=400)
        try:
            data = _json.loads(request.body)
            target_repo_id = int(data.get('target_repo_id', 0))
        except Exception:
            return JsonResponse({'error': 'Invalid request body'}, status=400)
        target_repo = get_object_or_404(Repository, pk=target_repo_id)
        available_ids = [r.id for r in get_available_bst_promotion_targets(build)]
        if target_repo_id not in available_ids:
            return JsonResponse(
                {'error': f'Cannot promote to {target_repo.name}: prerequisites not met or already promoted'},
                status=400,
            )
        try:
            promo = BstPromotion.objects.create(
                build=build,
                bst_source=build.bst_source,
                target_repo=target_repo,
                status='pending',
                promoted_by=request.user,
            )
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
        from apps.flatpak.tasks import promote_bst_task
        promote_bst_task.delay(promo.id)
        return JsonResponse({'status': 'ok', 'promotion_id': promo.id})


class ExternalRefPromoteView(LoginRequiredMixin, View):
    """Promote a published ExternalRef to a child repository."""

    def post(self, request, pk):
        import json
        from apps.flatpak.tasks import promote_external_ref_task

        ext = get_object_or_404(ExternalRef, pk=pk)
        if ext.status != 'published':
            return JsonResponse({'error': f'External ref must be published first (current: {ext.status})'}, status=400)

        try:
            data = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            data = {}

        target_repo_id = data.get('target_repo_id')
        if not target_repo_id:
            return JsonResponse({'error': 'target_repo_id is required'}, status=400)

        available_targets = get_available_external_ref_promotion_targets(ext)
        target_repo = next((repo for repo in available_targets if repo.pk == int(target_repo_id)), None)
        if target_repo is None:
            return JsonResponse({'error': 'Invalid promotion target for this external ref'}, status=400)

        promo = ExternalRefPromotion.objects.create(
            external_ref=ext,
            target_repo=target_repo,
            status='pending',
            promoted_by=request.user,
        )
        promote_external_ref_task.delay(promo.id)
        return JsonResponse({'status': 'ok', 'promotion_id': promo.id})


class ExternalRefPromotionRetryView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from apps.flatpak.tasks import promote_external_ref_task

        promo = get_object_or_404(ExternalRefPromotion, pk=pk)
        promo.status = 'pending'
        promo.error_message = ''
        promo.completed_at = None
        promo.save()
        promote_external_ref_task.delay(promo.id)
        return JsonResponse({'status': 'ok'})


class ExternalRefPromotionDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from apps.flatpak.utils.ostree import update_repo_metadata

        promo = get_object_or_404(ExternalRefPromotion.objects.select_related('external_ref', 'target_repo'), pk=pk)
        target_repo = promo.target_repo
        repo_path = target_repo.repo_path
        ref_name = promo.external_ref.ref

        try:
            delete_result = subprocess.run(
                ['ostree', 'refs', '--delete', ref_name, f'--repo={repo_path}'],
                capture_output=True, text=True, timeout=120,
            )
            if delete_result.returncode != 0 and 'No such ref' not in (delete_result.stderr or ''):
                raise RuntimeError(delete_result.stderr.strip() or delete_result.stdout.strip())

            update_repo_metadata(repo_path, target_repo.gpg_key)
            promo.delete()
            return JsonResponse({'status': 'ok'})
        except Exception as exc:
            return JsonResponse({'error': str(exc)}, status=400)


class BstPromotionRetryView(LoginRequiredMixin, View):
    """Re-queue a pending or failed BST promotion."""

    def post(self, request, pk):
        from apps.flatpak.models import BstPromotion
        from apps.flatpak.tasks import promote_bst_task
        promo = get_object_or_404(BstPromotion, pk=pk)
        if promo.status not in ('pending', 'failed'):
            return JsonResponse(
                {'error': f'Promotion is {promo.status}, only pending/failed can be retried'},
                status=400,
            )
        promo.status = 'pending'
        promo.error_message = ''
        promo.save(update_fields=['status', 'error_message'])
        promote_bst_task.delay(promo.id)
        return JsonResponse({'status': 'ok'})


class BstPromotionDeleteView(LoginRequiredMixin, View):
    """Delete a BST promotion record."""

    def post(self, request, pk):
        from apps.flatpak.models import BstPromotion
        promo = get_object_or_404(BstPromotion, pk=pk)
        promo.delete()
        return JsonResponse({'status': 'ok'})



def _delete_promotion_from_repo(promotion):
    """Remove the OSTree ref from the promotion's target repo and delete the Promotion record."""
    if promotion.status == 'promoted':
        target_repo_path = promotion.target_repo.repo_path
        ref_name = (
            f'app/{promotion.package.package_id}'
            f'/{promotion.package.arch}/{promotion.package.branch}'
        )
        locale_ref = (
            f'runtime/{promotion.package.package_id}.Locale'
            f'/{promotion.package.arch}/{promotion.package.branch}'
        )
        subprocess.run(
            ['ostree', 'refs', '--delete', ref_name, f'--repo={target_repo_path}'],
            capture_output=True, text=True, timeout=60
        )
        subprocess.run(
            ['ostree', 'refs', '--delete', locale_ref, f'--repo={target_repo_path}'],
            capture_output=True, text=True, timeout=60
        )
        result = update_repo_metadata(target_repo_path, promotion.target_repo.gpg_key)
        if not result['success']:
            logger.warning(f"update_repo_metadata warning for {promotion}: {result}")
    promotion.delete()


def _collect_child_promotions(build, parent_repo, visited=None):
    """Recursively collect all Promotion records for *build* that target descendant repos of *parent_repo*."""
    if visited is None:
        visited = set()
    if parent_repo.pk in visited:
        return []
    visited.add(parent_repo.pk)
    results = []
    for child_repo in parent_repo.child_repos.all():
        child_promo = Promotion.objects.filter(build=build, target_repo=child_repo).first()
        if child_promo:
            results.append(child_promo)
        results.extend(_collect_child_promotions(build, child_repo, visited))
    return results


class PromotionStatusBulkView(LoginRequiredMixin, View):
    """AJAX — return current status for multiple Promotion PKs.

    GET /promotions/status/?ids=1,2,3  →  {"1": {status, error_message, completed_at}, ...}
    """

    def get(self, request):
        raw = request.GET.get('ids', '')
        try:
            pks = [int(x) for x in raw.split(',') if x.strip()]
        except ValueError:
            return JsonResponse({}, status=400)
        result = {}
        for p in Promotion.objects.filter(pk__in=pks).select_related('promoted_by'):
            result[str(p.pk)] = {
                'status': p.status,
                'error_message': p.error_message,
                'promoted_by': p.promoted_by.username if p.promoted_by else None,
                'completed_at': p.completed_at.strftime('%b %d, %H:%M') if p.completed_at else None,
            }
        return JsonResponse(result)


class ExternalRefPromotionStatusBulkView(LoginRequiredMixin, View):
    """AJAX — return current status for multiple ExternalRefPromotion PKs.

    GET /external-promotions/status/?ids=1,2,3  →  {"1": {status, error_message, completed_at}, ...}
    """

    def get(self, request):
        raw = request.GET.get('ids', '')
        try:
            pks = [int(x) for x in raw.split(',') if x.strip()]
        except ValueError:
            return JsonResponse({}, status=400)
        result = {}
        for p in ExternalRefPromotion.objects.filter(pk__in=pks).select_related('promoted_by'):
            result[str(p.pk)] = {
                'status': p.status,
                'error_message': p.error_message,
                'promoted_by': p.promoted_by.username if p.promoted_by else None,
                'completed_at': p.completed_at.strftime('%b %d, %H:%M') if p.completed_at else None,
            }
        return JsonResponse(result)


class PromotionDeleteView(LoginRequiredMixin, View):
    """Delete a promotion (and all descendant-repo promotions) and remove OSTree refs."""

    def post(self, request, pk):
        promotion = get_object_or_404(Promotion, pk=pk)
        # Collect child promotions before we delete the parent (to avoid losing the ref chain)
        children = _collect_child_promotions(promotion.build, promotion.target_repo)
        try:
            # Delete children first (leaf → root order to avoid partial states)
            for child in reversed(children):
                _delete_promotion_from_repo(child)
            # Delete the requested promotion
            _delete_promotion_from_repo(promotion)
        except Exception as e:
            return JsonResponse({'error': f'Failed to remove ref from repo: {e}'}, status=500)
        return JsonResponse({
            'status': 'ok',
            'deleted_children': len(children),
        })


class BuildUnpublishView(LoginRequiredMixin, View):
    """Remove a published build from build-repo and set its status back to committed."""

    def post(self, request, pk):
        build = get_object_or_404(Build, pk=pk)
        if build.status != 'published':
            return JsonResponse({'error': 'Build is not published'}, status=400)

        package = build.package
        target_repo_path = package.repository.repo_path
        ref_name = f'app/{package.package_id}/{package.arch}/{package.branch}'

        try:
            # Remove from the TARGET (published) repo only.
            # build-repo is kept intact so the build can be re-published without rebuilding.
            subprocess.run(
                ['ostree', 'refs', '--delete', ref_name, f'--repo={target_repo_path}'],
                capture_output=True, text=True, timeout=60
            )
            # Also remove locale ref if present
            locale_ref = f'runtime/{package.package_id}.Locale/{package.arch}/{package.branch}'
            subprocess.run(
                ['ostree', 'refs', '--delete', locale_ref, f'--repo={target_repo_path}'],
                capture_output=True, text=True, timeout=60
            )
            # Regenerate target repo metadata so the ref is gone from the summary
            from apps.flatpak.utils.ostree import update_repo_metadata
            gpg_key = package.repository.gpg_key
            update_repo_metadata(target_repo_path, gpg_key, generate_deltas=False)
        except Exception as e:
            return JsonResponse({'error': f'Failed to remove ref from target repo: {e}'}, status=500)

        # Roll build and package status back to committed
        build.status = 'committed'
        build.completed_at = None
        build.save(update_fields=['status', 'completed_at'])

        # Update package status only if this is the current/latest build
        latest = package.builds.order_by('-build_number').first()
        if latest and latest.pk == build.pk:
            package.status = 'committed'
            package.save(update_fields=['status'])

        return JsonResponse({'status': 'ok', 'message': f'Build #{build.build_number} unpublished'})


class BuildDeleteView(LoginRequiredMixin, View):
    """Permanently delete a build and remove its OSTree refs from all repos."""

    ACTIVE = {'building', 'committing', 'publishing'}

    def post(self, request, pk):
        from apps.flatpak.utils.ostree import update_repo_metadata
        build = get_object_or_404(Build, pk=pk)

        if build.status in self.ACTIVE:
            return JsonResponse(
                {'error': 'Cannot delete an active build. Cancel it first.'},
                status=400,
            )

        package = build.package
        if not package:
            return JsonResponse({'error': 'Only package builds can be deleted here.'}, status=400)

        # 1. Delete all promotions for this build — removes OSTree refs from every
        #    target repo and deletes the Promotion DB records.
        for promo in list(build.promotions.select_related('target_repo', 'package').all()):
            try:
                _delete_promotion_from_repo(promo)
            except Exception as e:
                return JsonResponse(
                    {'error': f'Failed to remove promotion to {promo.target_repo.name}: {e}'},
                    status=500,
                )

        # 2. If the build is published, remove its ref from the package source repo
        #    — but only if the ref still points to this build's commit (a newer
        #    build may already have replaced it).
        if build.status == 'published':
            source_repo_path = package.repository.repo_path
            ref_name = f'app/{package.package_id}/{package.arch}/{package.branch}'
            locale_ref = f'runtime/{package.package_id}.Locale/{package.arch}/{package.branch}'
            try:
                should_delete = True
                if build.commit_hash:
                    r = subprocess.run(
                        ['ostree', 'rev-parse', f'--repo={source_repo_path}', ref_name],
                        capture_output=True, text=True, timeout=30,
                    )
                    should_delete = r.stdout.strip() == build.commit_hash
                if should_delete:
                    subprocess.run(
                        ['ostree', 'refs', '--delete', ref_name, f'--repo={source_repo_path}'],
                        capture_output=True, text=True, timeout=60,
                    )
                    subprocess.run(
                        ['ostree', 'refs', '--delete', locale_ref, f'--repo={source_repo_path}'],
                        capture_output=True, text=True, timeout=60,
                    )
                    update_repo_metadata(source_repo_path, package.repository.gpg_key, generate_deltas=False)
            except Exception as e:
                return JsonResponse(
                    {'error': f'Failed to remove ref from source repo: {e}'}, status=500
                )

        build_number = build.build_number
        build.delete()

        # Update Package.status to reflect whichever build is now the latest.
        latest = package.builds.order_by('-build_number').first()
        package.status = latest.status if latest else 'failed'
        package.save(update_fields=['status'])

        return JsonResponse({'status': 'ok', 'message': f'Build #{build_number} deleted'})


class BuildCancelView(LoginRequiredMixin, View):
    """Cancel an in-progress build (building / committing / publishing)."""

    CANCELLABLE = {'building', 'committing', 'publishing'}

    def post(self, request, pk):
        build = get_object_or_404(Build, pk=pk)
        if build.status not in self.CANCELLABLE:
            return JsonResponse(
                {'error': f'Build cannot be cancelled in status \'{build.status}\''},
                status=400
            )

        build.status = 'cancelled'
        from django.utils import timezone as tz
        build.completed_at = tz.now()
        build.save(update_fields=['status', 'completed_at'])

        # Revoke + terminate the Celery task so the subprocess is killed
        if build.celery_task_id:
            try:
                from celery import current_app
                current_app.control.revoke(
                    build.celery_task_id, terminate=True, signal='SIGTERM'
                )
            except Exception as exc:
                import logging as _log
                _log.getLogger(__name__).warning(
                    f"Could not revoke Celery task {build.celery_task_id}: {exc}"
                )

        # Update the parent entity (Package or BuildStreamSource) status
        entity_id = None
        if build.package_id:
            package = build.package
            latest = package.builds.order_by('-build_number').first()
            if latest and latest.pk == build.pk:
                package.status = 'cancelled'
                package.save(update_fields=['status'])
            entity_id = build.package_id
        elif build.bst_source_id:
            bst_source = build.bst_source
            latest = bst_source.builds.order_by('-build_number').first()
            if latest and latest.pk == build.pk:
                bst_source.status = 'cancelled'
                bst_source.save(update_fields=['status'])
            entity_id = build.bst_source_id

        # Notify WebSocket clients so the UI updates immediately
        if entity_id is not None:
            from apps.flatpak.tasks import send_build_status_update
            send_build_status_update(entity_id, 'cancelled', f'Build #{build.build_number} was cancelled.')

        return JsonResponse({
            'status': 'cancelled',
            'message': f'Build #{build.build_number} has been cancelled.',
        })


def _ostree_refs(repo_path):
    from apps.flatpak.utils.sync import ostree_refs
    return ostree_refs(repo_path)


class SyncReposView(LoginRequiredMixin, View):
    """Scan all OSTree repos on disk and reconcile Build / Promotion records."""

    def post(self, request):
        from apps.flatpak.utils.sync import run_repo_sync
        stats = run_repo_sync()
        return JsonResponse(stats)


class PromotionListView(LoginRequiredMixin, ListView):
    """List all promotions — the Published Builds page."""
    model = Promotion
    template_name = 'flatpak/promotion_list.html'
    context_object_name = 'promotions'
    paginate_by = 50

    def get_queryset(self):
        from django.db.models import Q
        qs = Promotion.objects.select_related(
            'build', 'package', 'package__repository', 'target_repo', 'promoted_by'
        ).order_by('-created_at')
        q = self.request.GET.get('q', '').strip()
        status = self.request.GET.get('status', '').strip()
        repo = self.request.GET.get('repo', '').strip()
        if q:
            qs = qs.filter(
                Q(package__package_name__icontains=q) | Q(package__package_id__icontains=q)
            )
        if status:
            qs = qs.filter(status=status)
        if repo:
            qs = qs.filter(target_repo_id=repo)
        return qs

    def get_context_data(self, **kwargs):
        from django.db.models import Q
        context = super().get_context_data(**kwargs)
        pub_qs = (
            Build.objects.filter(status='published', package__isnull=False)
            .select_related('package', 'package__repository', 'package__created_by')
            .order_by('-completed_at')
        )
        q = self.request.GET.get('q', '').strip()
        pub_repo = self.request.GET.get('pub_repo', '').strip()
        if q:
            pub_qs = pub_qs.filter(
                Q(package__package_name__icontains=q) | Q(package__package_id__icontains=q)
            )
        if pub_repo:
            pub_qs = pub_qs.filter(package__repository_id=pub_repo)
        context['published_builds'] = pub_qs
        context['published_externals'] = (
            ExternalRef.objects
            .filter(status='published')
            .select_related('repository', 'remote', 'created_by')
            .prefetch_related('promotions', 'promotions__target_repo')
            .order_by('-updated_at')
        )
        # Use Package.status (canonical truth) so we always see the current
        # state of each package, not stale Build rows from previous attempts.
        context['ready_to_commit'] = (
            Package.objects
            .filter(status='built')
            .select_related('repository')
            .prefetch_related('builds')
            .order_by('package_name')
        )
        context['ready_to_publish'] = (
            Package.objects
            .filter(status='committed')
            .select_related('repository')
            .prefetch_related('builds')
            .order_by('package_name')
        )
        # Build list items with available promotion targets for the
        # "Ready to Promote" card.  We use the latest published Build per
        # package so the promote endpoint has the correct build_pk.
        ready_to_promote = []
        promote_builds = (
            Build.objects
            .filter(status='published', package__isnull=False)
            .select_related('package', 'package__repository')
            .prefetch_related('promotions', 'promotions__target_repo')
            .order_by('package__package_name', '-build_number')
        )
        seen_packages = set()
        for build in promote_builds:
            # Only show the latest published build per package
            if build.package_id in seen_packages:
                continue
            targets = get_available_promotion_targets(build)
            if targets:
                # Repos this build has already been successfully promoted to
                # (use the prefetched set to avoid extra queries)
                current_repos = [
                    p.target_repo.name
                    for p in build.promotions.all()
                    if p.status == 'promoted'
                ]
                ready_to_promote.append({'build': build, 'targets': targets, 'current_repos': current_repos})
                seen_packages.add(build.package_id)
        context['ready_to_promote'] = ready_to_promote
        ready_to_promote_externals = []
        for ext in context['published_externals']:
            targets = get_available_external_ref_promotion_targets(ext)
            if targets:
                ready_to_promote_externals.append({'external_ref': ext, 'targets': targets})
        context['ready_to_promote_externals'] = ready_to_promote_externals
        context['repositories'] = Repository.objects.filter(is_active=True)
        context['promo_status_choices'] = Promotion.STATUS_CHOICES
        context['filter_q'] = self.request.GET.get('q', '')
        context['filter_status'] = self.request.GET.get('status', '')
        context['filter_repo'] = self.request.GET.get('repo', '')
        context['filter_pub_repo'] = self.request.GET.get('pub_repo', '')
        get_params = self.request.GET.copy()
        get_params.pop('page', None)
        context['filter_params'] = get_params.urlencode()

        # Build a JSON map of promotion_pk → [child repo names that also have that build]
        import json as _json
        from collections import defaultdict
        page_promos = list(context['promotions'])
        if page_promos:
            build_ids = list({p.build_id for p in page_promos})
            all_build_promos = (
                Promotion.objects
                .filter(build_id__in=build_ids)
                .values('build_id', 'target_repo_id', 'target_repo__name')
            )
            # build_id → {target_repo_id: name}
            build_repo_map = defaultdict(dict)
            for p in all_build_promos:
                build_repo_map[p['build_id']][p['target_repo_id']] = p['target_repo__name']
            child_repos_map = {}
            for promo in page_promos:
                kids = []
                for child_repo in promo.target_repo.child_repos.all():
                    if child_repo.pk in build_repo_map[promo.build_id]:
                        kids.append(build_repo_map[promo.build_id][child_repo.pk])
                child_repos_map[str(promo.pk)] = kids
            context['promotion_child_repos_json'] = _json.dumps(child_repos_map)
        else:
            context['promotion_child_repos_json'] = '{}'

        # BST promotions for the promotions page
        from apps.flatpak.models import BstPromotion
        context['bst_promotions'] = (
            BstPromotion.objects
            .select_related('build', 'bst_source', 'target_repo', 'promoted_by')
            .order_by('-created_at')[:50]
        )
        context['external_ref_promotions'] = (
            ExternalRefPromotion.objects
            .select_related('external_ref', 'target_repo', 'promoted_by', 'external_ref__repository')
            .order_by('-created_at')[:50]
        )
        # BST builds ready to promote
        ready_to_promote_bst = []
        bst_published = (
            Build.objects
            .filter(status='published', bst_source__isnull=False)
            .select_related('bst_source', 'bst_source__repository')
            .prefetch_related('bst_promotions', 'bst_promotions__target_repo')
            .order_by('bst_source__name', '-build_number')
        )
        seen_bst = set()
        for build in bst_published:
            if build.bst_source_id in seen_bst:
                continue
            targets = get_available_bst_promotion_targets(build)
            if targets:
                ready_to_promote_bst.append({'build': build, 'targets': targets})
                seen_bst.add(build.bst_source_id)
        context['ready_to_promote_bst'] = ready_to_promote_bst

        return context


class PackageCreateView(LoginRequiredMixin, CreateView):
    """Create new package."""
    model = Package
    template_name = 'flatpak/package_form.html'
    fields = ['repository', 'package_id', 'package_name', 'version', 'git_repo_url', 'git_branch', 'manifest_file', 'upstream_url', 'upstream_version_script', 'branch', 'arch', 'installation_type', 'organisations']
    
    def get_initial(self):
        initial = super().get_initial()
        for field in ('package_id', 'package_name', 'git_repo_url', 'git_branch', 'upstream_url', 'arch', 'branch'):
            if field in self.request.GET:
                initial[field] = self.request.GET[field]
        return initial

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
        context['all_organisations'] = Organisation.objects.all()
        context['selected_org_pks'] = set(self.request.POST.getlist('organisations')) if self.request.method == 'POST' else set()
        return context
    
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)

        if form.instance.git_repo_url:
            messages.success(
                self.request,
                f'Package {form.instance.package_id} created.'
            )
        else:
            messages.success(
                self.request,
                f'Package {form.instance.package_id} created. Ready for package upload.'
            )

        return response
    
    def form_invalid(self, form):
        """Handle invalid form submission."""
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Form validation failed: {form.errors}")
        messages.error(self.request, 'Please correct the errors below.')
        return super().form_invalid(form)
    
    def get_success_url(self):
        return reverse('flatpak:package_detail', kwargs={'pk': self.object.pk})


class PackageUpdateView(LoginRequiredMixin, UpdateView):
    """Edit package details."""
    model = Package
    template_name = 'flatpak/package_form.html'
    fields = ['repository', 'package_id', 'package_name', 'version', 'branch', 'arch', 'git_repo_url', 'git_branch', 'manifest_file', 'upstream_url', 'upstream_version_script', 'installation_type', 'organisations']
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['edit_mode'] = True
        context['package'] = self.object
        context['all_organisations'] = Organisation.objects.all()
        if self.request.method == 'POST':
            context['selected_org_pks'] = set(self.request.POST.getlist('organisations'))
        else:
            context['selected_org_pks'] = set(str(pk) for pk in self.object.organisations.values_list('pk', flat=True))
        return context
    
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        return form
    
    def form_valid(self, form):
        package = self.get_object()
        
        messages.success(
            self.request,
            f'Package {package.package_id} updated successfully.'
        )
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse('flatpak:package_detail', kwargs={'pk': self.object.pk})


class PackageDeleteView(LoginRequiredMixin, DeleteView):
    """Cancel/delete package."""
    model = Package
    template_name = 'flatpak/package_confirm_delete.html'
    success_url = reverse_lazy('flatpak:package_list')
    
    # Statuses where the package can be fully deleted
    _DELETABLE_STATUSES = ['built', 'committed', 'published', 'failed', 'cancelled']
    # Statuses where the package is actively running and should only be cancelled
    _CANCELLABLE_STATUSES = ['pending', 'building', 'committing', 'publishing']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        package = self.get_object()
        context['can_cancel'] = package.status in self._CANCELLABLE_STATUSES
        context['can_delete'] = package.status in self._DELETABLE_STATUSES
        return context

    def post(self, request, *args, **kwargs):
        """Override post to handle cancellation vs deletion."""
        return self.delete(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        logger = logging.getLogger(__name__)

        package = self.get_object()
        package_id = package.package_id
        current_status = package.status

        logger.info(f"Delete/cancel called for package {package_id} (pk={package.pk}) status={current_status} by {request.user.username}")

        # If package is actively running, cancel rather than delete
        if current_status in self._CANCELLABLE_STATUSES:
            package.status = 'cancelled'
            package.save()
            logger.info(f"Package {package_id} (pk={package.pk}) cancelled (was {current_status})")
            messages.success(request, f'Package {package_id} has been cancelled.')
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json':
                from django.http import JsonResponse
                return JsonResponse({'status': 'cancelled', 'message': f'Package {package_id} cancelled'})
            return HttpResponseRedirect(self.success_url)

        # Reject anything that isn't in a deletable terminal state
        if current_status not in self._DELETABLE_STATUSES:
            logger.warning(f"Attempted to delete package {package_id} (pk={package.pk}) with non-deletable status {current_status}")
            messages.error(request, f'Cannot delete package with status: {current_status}')
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json':
                from django.http import JsonResponse
                return JsonResponse({'error': f'Cannot delete package with status: {current_status}'}, status=400)
            return HttpResponseRedirect(reverse('flatpak:package_detail', kwargs={'pk': package.pk}))

        # Delete the package — all related Builds, BuildLogs, BuildArtifacts,
        # BuildExternalRefs and Promotions cascade automatically via FK CASCADE.
        logger.info(f"Deleting package {package_id} (pk={package.pk}) and all related records (status was {current_status})")
        messages.success(request, f'Package {package_id} deleted successfully.')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json':
            from django.http import JsonResponse
            package.delete()
            return JsonResponse({'status': 'deleted', 'redirect': str(self.success_url)})
        return super().delete(request, *args, **kwargs)


class PackageRetryAllFailedView(LoginRequiredMixin, View):
    """Retry all packages currently in failed or cancelled status."""

    def post(self, request):
        from django.http import JsonResponse
        packages = Package.objects.filter(status__in=['failed', 'cancelled'])
        retried = []
        for package in packages:
            package.build_number += 1
            package.status = 'pending'
            package.error_message = ''
            package.save()
            retried.append(package.package_name)
        return JsonResponse({
            'status': 'success',
            'retried': len(retried),
            'packages': retried,
        })


class PackageBulkActionView(LoginRequiredMixin, View):
    """Perform a bulk action (rebuild or delete) on a list of package IDs."""

    def post(self, request):
        import json
        from django.http import JsonResponse

        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        action = data.get('action')
        ids = data.get('ids', [])

        if not isinstance(ids, list) or not ids:
            return JsonResponse({'error': 'No package IDs provided'}, status=400)

        if action not in ('rebuild', 'delete', 'assign_orgs'):
            return JsonResponse({'error': f'Unknown action: {action}'}, status=400)

        packages = Package.objects.filter(pk__in=ids)

        if action == 'assign_orgs':
            org_pks = data.get('org_pks', [])
            orgs = Organisation.objects.filter(pk__in=org_pks)
            for package in packages:
                package.organisations.set(orgs)
            return JsonResponse({'status': 'success', 'updated': packages.count()})

        results = {'ok': [], 'skipped': []}

        for package in packages:
            if action == 'rebuild':
                package.build_number += 1
                package.status = 'pending'
                package.error_message = ''
                package.save()
                results['ok'].append(package.package_name)
            elif action == 'delete':
                if package.status in ['pending', 'building', 'committing', 'publishing']:
                    package.status = 'cancelled'
                    package.save()
                    results['ok'].append(package.package_name)
                elif package.status in ['failed', 'cancelled', 'published', 'built', 'committed']:
                    results['ok'].append(package.package_name)
                    package.delete()
                else:
                    results['skipped'].append(package.package_name)

        return JsonResponse({'status': 'success', **results})


class PackageRetryView(LoginRequiredMixin, View):
    """Retry a failed or cancelled build."""
    
    def post(self, request, pk):
        from django.http import JsonResponse
        package = get_object_or_404(Package, pk=pk)
        
        # Only allow retry for failed or cancelled packages
        if package.status not in ['failed', 'cancelled', 'built', 'committed', 'published']:
            return JsonResponse(
                {'error': f'Package cannot be retried in {package.status} status.'},
                status=400
            )
        
        # Increment build number and reset to pending
        package.build_number += 1
        package.status = 'pending'
        package.error_message = ''
        package.save()
        
        return JsonResponse({
            'status': 'success',
            'message': f'Package {package.package_name} (attempt #{package.build_number}) will be retried shortly.',
            'build_number': package.build_number,
        })


class PackageCommitView(LoginRequiredMixin, View):
    """Commit a built flatpak."""
    
    def post(self, request, pk):
        from apps.flatpak.tasks import commit_package_task
        from django.http import JsonResponse
        
        package = get_object_or_404(Package, pk=pk)
        
        # Only allow commit for built packages
        if package.status not in ['pending', 'building', 'built']:
            return JsonResponse(
                {'error': f'Package must be in built state to commit (current: {package.status})'}, 
                status=400
            )
        
        # Queue commit task
        commit_package_task.delay(package.id)
        
        return JsonResponse({
            'status': 'success',
            'message': f'Package {package.package_name} commit started'
        })


class PackageStatusView(LoginRequiredMixin, View):
    """Return the current status of a package as JSON (used for polling)."""

    def get(self, request, pk):
        package = get_object_or_404(Package, pk=pk)
        return JsonResponse({'status': package.status})


class PackagePublishView(LoginRequiredMixin, View):
    """Publish a committed build to the repository."""
    
    def post(self, request, pk):
        from apps.flatpak.tasks import publish_package_task
        from django.http import JsonResponse
        
        package = get_object_or_404(Package, pk=pk)
        
        # Only allow publish for committed packages
        if package.status != 'committed':
            return JsonResponse(
                {'error': f'Package must be committed before publishing (current: {package.status})'}, 
                status=400
            )
        
        # Queue publish task
        publish_package_task.delay(package.id)
        
        return JsonResponse({
            'status': 'success',
            'message': f'Package {package.package_name} publish started'
        })


class PackageRepublishView(LoginRequiredMixin, View):
    """Re-run publish on a package that failed during the publish/commit stage.

    The build artifacts are still in build-repo — there's no need to rebuild from
    source.  We just reset the status to 'committed' and re-queue publish.
    """

    def post(self, request, pk):
        from apps.flatpak.tasks import publish_package_task

        package = get_object_or_404(Package, pk=pk)

        # Only allow re-publish when the failure happened at publish/commit stage.
        # The build itself must have succeeded (produced_refs populated).
        if package.status not in ('failed', 'committed', 'publishing'):
            return JsonResponse(
                {'error': f'Re-publish is only valid for packages in failed/committed/publishing status (current: {package.status})'},
                status=400,
            )
        if not package.produced_refs:
            return JsonResponse(
                {'error': 'No produced refs found — the build may not have completed. Use Retry to rebuild.'},
                status=400,
            )

        # Reset to committed so publish_package_task's guard passes.
        # Skip delta regeneration — the content hasn't changed on republish.
        package.status = 'committed'
        package.error_message = ''
        package.save(update_fields=['status', 'error_message', 'updated_at'])

        publish_package_task.delay(package.id, generate_deltas=False)

        return JsonResponse({
            'status': 'success',
            'message': f'Re-publishing {package.package_name} (build #{package.build_number})',
        })


class ConfigView(LoginRequiredMixin, View):
    """Display and update site-wide configuration."""

    def _context(self, form):
        from .models import FlatpakRemote
        from .forms import FlatpakRemoteForm
        return {
            'form': form,
            'remotes': FlatpakRemote.objects.all(),
            'remote_form': FlatpakRemoteForm(),
        }

    def get(self, request):
        from .forms import SiteConfigForm
        from .models import SiteConfig
        form = SiteConfigForm(instance=SiteConfig.get_solo())
        return render(request, 'flatpak/config.html', self._context(form))

    def post(self, request):
        from .forms import SiteConfigForm
        from .models import SiteConfig
        form = SiteConfigForm(request.POST, instance=SiteConfig.get_solo())
        if form.is_valid():
            form.save()
            messages.success(request, 'Configuration saved successfully.')
        return render(request, 'flatpak/config.html', self._context(form))


class FlatpakRemoteCreateView(LoginRequiredMixin, View):
    """Add a new Flatpak remote."""

    def post(self, request):
        from .forms import FlatpakRemoteForm
        form = FlatpakRemoteForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"Remote '{form.cleaned_data['name']}' added.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        return redirect('flatpak:config')


class FlatpakRemoteDeleteView(LoginRequiredMixin, View):
    """Remove a Flatpak remote."""

    def post(self, request, pk):
        from .models import FlatpakRemote
        remote = get_object_or_404(FlatpakRemote, pk=pk)
        name = remote.name
        remote.delete()
        messages.success(request, f"Remote '{name}' removed.")
        return redirect('flatpak:config')


class FlatpakRemoteToggleView(LoginRequiredMixin, View):
    """Toggle active status of a Flatpak remote."""

    def post(self, request, pk):
        from .models import FlatpakRemote
        remote = get_object_or_404(FlatpakRemote, pk=pk)
        remote.is_active = not remote.is_active
        remote.save(update_fields=['is_active'])
        return redirect('flatpak:config')


class RunCleanupNowView(LoginRequiredMixin, View):
    """Trigger cleanup_failed_builds task immediately (synchronous, not via queue)."""

    def post(self, request):
        from apps.flatpak.tasks import cleanup_failed_builds
        result = cleanup_failed_builds()  # run synchronously
        return JsonResponse({'status': 'ok', 'message': result})


class RunCheckExternalRefUpdatesView(LoginRequiredMixin, View):
    """Immediately run the upstream commit check for all tracked ExternalRefs."""

    def post(self, request):
        from apps.flatpak.tasks import check_external_ref_updates
        result = check_external_ref_updates()  # run synchronously
        return JsonResponse({'status': 'ok', 'message': result})


class RunAvailableVersionScanView(LoginRequiredMixin, View):
    """Queue an available-version check for every git-based package immediately."""

    def post(self, request):
        from apps.flatpak.models import Package
        from apps.flatpak.tasks import check_available_version_task
        packages = Package.objects.filter(
            git_repo_url__isnull=False
        ).exclude(git_repo_url='')
        count = packages.count()
        for p in packages:
            check_available_version_task.delay(p.id)
        return JsonResponse({'status': 'ok', 'message': f"Queued {count} available version check(s)"})


class RunUpstreamVersionScanView(LoginRequiredMixin, View):
    """Queue an upstream-version check for every eligible package immediately."""

    def post(self, request):
        from apps.flatpak.models import Package
        from apps.flatpak.tasks import check_upstream_version_task
        packages = Package.objects.filter(upstream_url__isnull=False).exclude(upstream_url='')
        script_only = Package.objects.filter(upstream_url='').exclude(upstream_version_script='')
        all_packages = (packages | script_only).distinct()
        count = all_packages.count()
        for p in all_packages:
            check_upstream_version_task.delay(p.id)
        return JsonResponse({'status': 'ok', 'message': f"Queued {count} upstream version check(s)"})


class ScanOrphanedRefsView(LoginRequiredMixin, View):
    """Return refs present in any repo that are not tracked by a Package, ExternalRef, or BST source."""

    def post(self, request):
        from .models import Repository, Package, ExternalRef, BuildStreamSource
        import subprocess

        # Build the set of all known refs across all DB objects.
        known_refs = set()
        for pkg in Package.objects.all():
            known_refs.add(f'app/{pkg.package_id}/{pkg.arch}/{pkg.branch}')
            known_refs.add(f'runtime/{pkg.package_id}.Locale/{pkg.arch}/{pkg.branch}')
            known_refs.add(f'runtime/{pkg.package_id}.Debug/{pkg.arch}/{pkg.branch}')
        for ext in ExternalRef.objects.all():
            known_refs.add(ext.ref)
        for bst in BuildStreamSource.objects.all():
            for ref in bst.produced_refs.splitlines():
                ref = ref.strip()
                if ref:
                    known_refs.add(ref)

        # Internal refs we always keep regardless of any DB record.
        ALWAYS_KEEP = {'ostree-metadata', 'appstream/x86_64', 'appstream2/x86_64'}

        orphans_by_repo = {}
        for repo in Repository.objects.filter(is_active=True):
            repo_path = repo.repo_path
            if not os.path.exists(os.path.join(repo_path, 'config')):
                continue
            result = subprocess.run(
                ['ostree', 'refs', f'--repo={repo_path}'],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                continue
            orphans = []
            for ref in result.stdout.splitlines():
                ref = ref.strip()
                if not ref:
                    continue
                if ref in ALWAYS_KEEP:
                    continue
                if ref in known_refs:
                    continue
                orphans.append(ref)
            if orphans:
                orphans_by_repo[repo.name] = orphans

        total = sum(len(v) for v in orphans_by_repo.values())
        return JsonResponse({
            'status': 'ok',
            'total': total,
            'orphans': orphans_by_repo,
        })


class PruneOrphanedRefsView(LoginRequiredMixin, View):
    """Dispatch a background task to delete a specific orphaned ref."""

    def post(self, request):
        import json
        import uuid
        from .tasks import prune_orphaned_refs_task
        try:
            body = json.loads(request.body)
            repo_name = body.get('repo')
            ref = body.get('ref')
        except (ValueError, AttributeError):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)

        if not repo_name or not ref:
            return JsonResponse({'error': 'repo and ref are required'}, status=400)

        task_id = str(uuid.uuid4())
        prune_orphaned_refs_task.delay(task_id, [{'repo': repo_name, 'ref': ref}])
        return JsonResponse({'status': 'pending', 'task_id': task_id})


class BulkPruneOrphanedRefsView(LoginRequiredMixin, View):
    """Dispatch a background task to delete multiple orphaned refs at once."""

    def post(self, request):
        import json
        import uuid
        from .tasks import prune_orphaned_refs_task
        try:
            body = json.loads(request.body)
            items = body.get('items')  # [{repo, ref}, ...]
        except (ValueError, AttributeError):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)

        if not items or not isinstance(items, list):
            return JsonResponse({'error': 'items must be a non-empty list'}, status=400)

        valid = [i for i in items if i.get('repo') and i.get('ref')]
        if not valid:
            return JsonResponse({'error': 'No valid items provided'}, status=400)

        task_id = str(uuid.uuid4())
        prune_orphaned_refs_task.delay(task_id, valid)
        return JsonResponse({'status': 'pending', 'task_id': task_id})


@login_required
def dependencies_list(request):
    """List missing dependency refs and installed Flatpak runtimes/SDKs/extensions."""
    import subprocess
    from urllib.parse import urlencode
    from apps.flatpak.models import ExternalRef

    # ------------------------------------------------------------------ #
    # Curated catalog of well-known common Flatpak runtime dependencies.  #
    # Each entry carries enough info to pre-fill either create form.      #
    # ------------------------------------------------------------------ #
    KNOWN_DEPS = [
        {
            'id': 'org.gnome.Platform',
            'name': 'GNOME Platform',
            'type': 'Runtime',
            'build_type': 'bst',
            'git_repo_url': 'https://gitlab.gnome.org/GNOME/gnome-build-meta.git',
            'git_branch': 'master',
            'bst_element': 'elements/flatpak/platform/platform.bst',
            'description': 'GNOME Platform runtime — built with BuildStream (gnome-build-meta)',
        },
        {
            'id': 'org.gnome.Sdk',
            'name': 'GNOME SDK',
            'type': 'SDK',
            'build_type': 'bst',
            'git_repo_url': 'https://gitlab.gnome.org/GNOME/gnome-build-meta.git',
            'git_branch': 'master',
            'bst_element': 'elements/flatpak/sdk/sdk.bst',
            'description': 'GNOME SDK — built with BuildStream (gnome-build-meta)',
        },
        {
            'id': 'org.kde.Platform',
            'name': 'KDE Plasma Platform',
            'type': 'Runtime',
            'build_type': 'flatpak',
            'git_repo_url': 'https://invent.kde.org/packaging/flatpak-kde-runtime.git',
            'git_branch': 'qt6.10',
            'bst_element': None,
            'description': 'KDE Platform runtime — built with flatpak-builder (flatpak-kde-runtime)',
        },
        {
            'id': 'org.kde.Sdk',
            'name': 'KDE SDK',
            'type': 'SDK',
            'build_type': 'flatpak',
            'git_repo_url': 'https://invent.kde.org/packaging/flatpak-kde-runtime.git',
            'git_branch': 'qt6.10',
            'bst_element': None,
            'description': 'KDE SDK — built with flatpak-builder (flatpak-kde-runtime)',
        },
        {
            'id': 'org.freedesktop.Platform.openh264',
            'name': 'OpenH264 Extension',
            'type': 'Extension',
            'build_type': 'bst',
            'git_repo_url': 'https://gitlab.com/freedesktop-sdk/openh264-extension.git',
            'git_branch': 'master',
            'bst_element': 'elements/openh264-extension.bst',
            'description': 'Cisco OpenH264 codec extension — BST (openh264-extension)',
            'warning': 'Only supported on freedesktop-sdk 24.08 and earlier; obsolete in 25.08+',
        },
        {
            'id': 'org.freedesktop.Sdk.Extension.golang',
            'name': 'Go SDK Extension',
            'type': 'Extension',
            'build_type': 'flatpak',
            'git_repo_url': 'https://github.com/flathub/org.freedesktop.Sdk.Extension.golang.git',
            'git_branch': 'master',
            'bst_element': None,
            'description': 'Go programming language SDK extension — flatpak-builder (Flathub)',
        },
        {
            'id': 'org.freedesktop.Sdk.Extension.openjdk17',
            'name': 'OpenJDK 17 Extension',
            'type': 'Extension',
            'build_type': 'flatpak',
            'git_repo_url': 'https://github.com/flathub/org.freedesktop.Sdk.Extension.openjdk17.git',
            'git_branch': 'master',
            'bst_element': None,
            'description': 'Java 17 (OpenJDK) SDK extension — flatpak-builder (Flathub)',
        },
        {
            'id': 'org.freedesktop.Sdk.Extension.openjdk21',
            'name': 'OpenJDK 21 Extension',
            'type': 'Extension',
            'build_type': 'flatpak',
            'git_repo_url': 'https://github.com/flathub/org.freedesktop.Sdk.Extension.openjdk21.git',
            'git_branch': 'master',
            'bst_element': None,
            'description': 'Java 21 (OpenJDK) SDK extension — flatpak-builder (Flathub)',
        },
        {
            'id': 'org.freedesktop.Sdk.Extension.rust-stable',
            'name': 'Rust SDK Extension',
            'type': 'Extension',
            'build_type': 'flatpak',
            'git_repo_url': 'https://github.com/flathub/org.freedesktop.Sdk.Extension.rust-stable.git',
            'git_branch': 'master',
            'bst_element': None,
            'description': 'Rust (stable) SDK extension — flatpak-builder (Flathub)',
        },
        {
            'id': 'org.freedesktop.Sdk.Extension.node20',
            'name': 'Node.js 20 SDK Extension',
            'type': 'Extension',
            'build_type': 'flatpak',
            'git_repo_url': 'https://github.com/flathub/org.freedesktop.Sdk.Extension.node20.git',
            'git_branch': 'master',
            'bst_element': None,
            'description': 'Node.js 20 SDK extension — flatpak-builder (Flathub)',
        },
    ]

    # Helper: strip leading type segment, keep last 3 path components (name/arch/branch)
    def _ref_tail(ref_str):
        parts = ref_str.split('/')
        return '/'.join(parts[-3:]) if len(parts) >= 3 else ref_str

    # For each known dep, check managed status against flat-manager DB
    all_bst = list(BuildStreamSource.objects.only('pk', 'name', 'produced_refs'))
    all_pkg = list(Package.objects.only('pk', 'package_id', 'package_name'))
    all_ext = list(ExternalRef.objects.only('pk', 'ref', 'display_name', 'status'))

    # ExternalRef lookup by tail (name/arch/branch, stripping leading type prefix)
    ext_by_tail = {}
    for _e in all_ext:
        ext_by_tail[_ref_tail(_e.ref)] = _e

    bst_create_url = reverse('flatpak:bst_source_create')
    pkg_create_url = reverse('flatpak:package_create')
    external_create_url = reverse('flatpak:external_create')

    for dep in KNOWN_DEPS:
        app_id = dep['id']
        # Check BST: produced_refs contains the app ID string, or the BST name matches
        dep['managed_bst'] = next(
            (b for b in all_bst if app_id in b.produced_refs or app_id.lower() in b.name.lower()),
            None,
        )
        # Check Package: exact package_id match
        dep['managed_pkg'] = next((p for p in all_pkg if p.package_id == app_id), None)
        dep['is_managed'] = dep['managed_bst'] is not None or dep['managed_pkg'] is not None

        # Build pre-filled URLs
        bst_params = {'name': dep['name']}
        for k in ('git_repo_url', 'git_branch', 'bst_element'):
            if dep.get(k):
                bst_params[k] = dep[k]
        dep['add_bst_url'] = bst_create_url + '?' + urlencode(bst_params)

        pkg_params = {'package_id': app_id, 'package_name': dep['name']}
        for k in ('git_repo_url', 'git_branch'):
            if dep.get(k):
                pkg_params[k] = dep[k]
        dep['add_pkg_url'] = pkg_create_url + '?' + urlencode(pkg_params)

        # Pre-build the "Import as External" URL with a best-guess ref.
        ref_type = 'runtime' if dep['type'] in ('SDK', 'Runtime', 'Extension') else 'app'
        dep['add_external_url'] = external_create_url + '?' + urlencode({'ref': f"{ref_type}/{dep['id']}/x86_64/stable"})

    # ------------------------------------------------------------------ #
    # Missing Dependencies: deps required by built packages but not yet  #
    # tracked as an ExternalRef.                                          #
    # ------------------------------------------------------------------ #
    _missing = {}  # key: tail (name/arch/branch)
    for _pkg in Package.objects.only('pk', 'package_id', 'package_name', 'dependencies'):
        _deps = _pkg.dependencies
        if not _deps:
            continue
        _items = []
        for _key, _dtype in (('sdk_full', 'SDK'), ('runtime_full', 'Runtime'), ('base_full', 'BaseApp')):
            _v = _deps.get(_key)
            if _v:
                _items.append((_v, _dtype))
        for _ext_entry in (_deps.get('sdk_extensions') or []):
            _f = _ext_entry.get('full')
            if _f:
                _items.append((_f, 'Extension'))
        for _full_ref, _dtype in _items:
            _tail = _ref_tail(_full_ref)
            if _tail in ext_by_tail:
                continue  # already tracked as ExternalRef
            if _tail not in _missing:
                _parts = _full_ref.split('/')
                _missing[_tail] = {
                    'full_ref': _full_ref,
                    'app_id': _parts[0] if _parts else _full_ref,
                    'type': _dtype,
                    'branch': _parts[-1] if len(_parts) >= 1 else '',
                    'ostree_ref': f"runtime/{_full_ref}",
                    'required_by': [],
                    'add_external_url': (
                        external_create_url + '?' + urlencode({'ref': f"runtime/{_full_ref}"})
                    ),
                }
            _rb = _missing[_tail]['required_by']
            if not any(_r['pk'] == _pkg.pk for _r in _rb):
                _rb.append({'pk': _pkg.pk, 'name': _pkg.package_name or _pkg.package_id})
    missing_deps = sorted(_missing.values(), key=lambda d: (d['type'], d['app_id']))

    # ------------------------------------------------------------------ #
    # Also list locally-installed Flatpak runtimes/SDKs/extensions       #
    # ------------------------------------------------------------------ #
    dependencies = {'system': [], 'user': [], 'errors': []}

    for scope in ('system', 'user'):
        try:
            result = subprocess.run(
                ['flatpak', 'list', f'--{scope}', '--columns=name,application,version,branch,origin'],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if not line:
                        continue
                    parts = line.split('\t')
                    if len(parts) < 5:
                        continue
                    app_type = (
                        'SDK'       if 'Sdk'       in parts[1] else
                        'Runtime'   if 'Platform'  in parts[1] or 'runtime' in parts[1].lower() else
                        'Extension' if 'Extension' in parts[1] else
                        'BaseApp'   if 'BaseApp'   in parts[1] else
                        'App'
                    )
                    if app_type in ('SDK', 'Runtime', 'Extension', 'BaseApp'):
                        dependencies[scope].append({
                            'name': parts[0], 'id': parts[1], 'version': parts[2],
                            'branch': parts[3], 'origin': parts[4], 'type': app_type,
                        })
                dependencies[scope].sort(key=lambda x: (x['type'], x['name']))
        except subprocess.TimeoutExpired:
            dependencies['errors'].append(f'Timeout listing {scope} flatpaks')
        except Exception as exc:
            dependencies['errors'].append(f'Error listing {scope} flatpaks: {exc}')

    # Enrich installed flatpaks with managed status and pre-filled add URLs.
    # Cross-reference KNOWN_DEPS to supply BST element info where available.
    known_deps_by_id = {d['id']: d for d in KNOWN_DEPS}
    for scope in ('system', 'user'):
        for dep in dependencies[scope]:
            app_id = dep['id']
            dep['managed_bst'] = next(
                (b for b in all_bst if app_id in b.produced_refs or app_id.lower() in b.name.lower()),
                None,
            )
            dep['managed_pkg'] = next((p for p in all_pkg if p.package_id == app_id), None)
            # Check ExternalRef by tail match (name/arch/branch)
            _etail = f"{app_id}/x86_64/{dep['branch']}"
            dep['managed_external'] = ext_by_tail.get(_etail)
            dep['is_managed'] = (
                dep['managed_bst'] is not None
                or dep['managed_pkg'] is not None
                or dep['managed_external'] is not None
            )

            known = known_deps_by_id.get(app_id, {})

            bst_params = {'name': dep['name']}
            if known.get('git_repo_url'):
                bst_params['git_repo_url'] = known['git_repo_url']
            if known.get('git_branch'):
                bst_params['git_branch'] = known['git_branch']
            if known.get('bst_element'):
                bst_params['bst_element'] = known['bst_element']
            dep['has_bst_info'] = bool(known.get('bst_element'))
            dep['add_bst_url'] = (bst_create_url + '?' + urlencode(bst_params)) if dep['has_bst_info'] else None

            pkg_params = {'package_id': app_id, 'package_name': dep['name']}
            if known.get('git_repo_url'):
                pkg_params['git_repo_url'] = known['git_repo_url']
            if known.get('git_branch'):
                pkg_params['git_branch'] = known['git_branch']
            dep['add_pkg_url'] = pkg_create_url + '?' + urlencode(pkg_params)

            # Import as External ref (pre-fill the ref using installed branch)
            ref_type = 'runtime' if dep['type'] in ('SDK', 'Runtime', 'Extension', 'BaseApp') else 'app'
            ext_ref_str = f"{ref_type}/{app_id}/x86_64/{dep['branch']}"
            dep['add_external_url'] = external_create_url + '?' + urlencode({'ref': ext_ref_str})

    from .models import FlatpakRemote as _FR
    context = {
        'missing_deps': missing_deps,
        'dependencies': dependencies,
        'total_system': len(dependencies['system']),
        'total_user': len(dependencies['user']),
        'repositories': Repository.objects.filter(parent_repos__isnull=True, is_active=True).order_by('name'),
        'remotes': _FR.objects.filter(is_active=True).order_by('priority', 'name'),
    }
    return render(request, 'flatpak/dependencies_list.html', context)


def serve_repository(request, repo_path):
    """Serve OSTree repository files for flatpak installation."""
    import mimetypes
    from django.http import FileResponse, Http404, HttpResponse
    from pathlib import Path
    
    # Construct the full path
    full_path = Path(settings.REPOS_BASE_PATH) / repo_path
    
    # Security: Ensure the path is within REPOS_BASE_PATH (prevent directory traversal)
    try:
        full_path = full_path.resolve()
        repos_base = Path(settings.REPOS_BASE_PATH).resolve()
        if not str(full_path).startswith(str(repos_base)):
            raise Http404("Invalid repository path")
    except (ValueError, OSError):
        raise Http404("Invalid repository path")
    
    # Check if file exists
    if not full_path.exists():
        raise Http404("File not found")
    
    # If it's a directory, return index or 403
    if full_path.is_dir():
        # For directories, try to serve index or list contents
        index_path = full_path / 'index.html'
        if index_path.exists():
            full_path = index_path
        else:
            # Return basic directory listing for OSTree repos
            try:
                files = sorted([f.name for f in full_path.iterdir()])
                html = '<html><head><title>Index of {}</title></head><body>'.format(repo_path)
                html += '<h1>Index of {}</h1><ul>'.format(repo_path)
                if repo_path != '':
                    html += '<li><a href="../">Parent Directory</a></li>'
                for f in files:
                    html += '<li><a href="{}">{}</a></li>'.format(f, f)
                html += '</ul></body></html>'
                return HttpResponse(html, content_type='text/html')
            except PermissionError:
                raise Http404("Permission denied")
    
    # Determine content type
    content_type, encoding = mimetypes.guess_type(str(full_path))
    if content_type is None:
        content_type = 'application/octet-stream'
    
    # Serve the file
    try:
        response = FileResponse(open(full_path, 'rb'), content_type=content_type)
        response['Content-Length'] = full_path.stat().st_size
        return response
    except (IOError, OSError):
        raise Http404("Error reading file")


# ---------------------------------------------------------------------------
# ExternalRef views
# ---------------------------------------------------------------------------

class ExternalRefListView(LoginRequiredMixin, ListView):
    template_name = 'flatpak/external_list.html'
    context_object_name = 'externals'
    paginate_by = 30

    def get_queryset(self):
        from .models import ExternalRef
        from django.db.models import Q
        qs = ExternalRef.objects.select_related('repository', 'remote', 'created_by').order_by('-updated_at')
        q = self.request.GET.get('q', '').strip()
        status = self.request.GET.get('status', '').strip()
        repo = self.request.GET.get('repo', '').strip()
        if q:
            qs = qs.filter(Q(ref__icontains=q) | Q(display_name__icontains=q))
        if status:
            qs = qs.filter(status=status)
        if repo:
            qs = qs.filter(repository_id=repo)
        return qs

    def get_context_data(self, **kwargs):
        from .models import ExternalRef
        ctx = super().get_context_data(**kwargs)
        ctx['repositories'] = Repository.objects.filter(is_active=True)
        ctx['status_choices'] = ExternalRef.STATUS_CHOICES
        ctx['filter_q'] = self.request.GET.get('q', '')
        ctx['filter_status'] = self.request.GET.get('status', '')
        ctx['filter_repo'] = self.request.GET.get('repo', '')
        get_params = self.request.GET.copy()
        get_params.pop('page', None)
        ctx['filter_params'] = get_params.urlencode()
        ctx['all_organisations'] = Organisation.objects.all()
        return ctx


class ExternalRefBulkActionView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        from .models import ExternalRef
        import json
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        ids = data.get('ids', [])
        action = data.get('action', '')
        if not isinstance(ids, list) or not ids or not action:
            return JsonResponse({'error': 'ids and action are required'}, status=400)

        qs = ExternalRef.objects.filter(pk__in=ids)

        if action == 'delete':
            count = qs.count()
            qs.delete()
            return JsonResponse({'status': 'success', 'deleted': count})

        if action == 'pull':
            from .tasks import pull_external_ref_task
            for ext in qs:
                pull_external_ref_task.delay(ext.pk)
            return JsonResponse({'status': 'success', 'queued': qs.count()})

        if action == 'assign_orgs':
            org_pks = data.get('org_pks', [])
            orgs = Organisation.objects.filter(pk__in=org_pks)
            for ext in qs:
                ext.organisations.set(orgs)
            return JsonResponse({'status': 'success', 'updated': qs.count()})

        return JsonResponse({'error': f'Unknown action: {action}'}, status=400)


class ExternalRefBulkImportView(LoginRequiredMixin, View):
    """Create and immediately pull multiple ExternalRefs sharing a common repository and remote.

    POST body (JSON): { repository_id, remote_id, refs: [str, ...] }
    Returns: { created, skipped, redirect_url }
    """

    def post(self, request):
        import json
        from .models import ExternalRef, FlatpakRemote
        from apps.flatpak.tasks import pull_external_ref_task

        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        repository_id = data.get('repository_id')
        remote_id = data.get('remote_id')
        refs = data.get('refs', [])

        if not repository_id or not remote_id:
            return JsonResponse({'error': 'repository_id and remote_id are required'}, status=400)
        if not refs or not isinstance(refs, list):
            return JsonResponse({'error': 'refs list is required'}, status=400)

        try:
            repository = Repository.objects.get(pk=repository_id, parent_repos__isnull=True, is_active=True)
        except Repository.DoesNotExist:
            return JsonResponse({'error': 'Invalid repository'}, status=400)

        try:
            remote = FlatpakRemote.objects.get(pk=remote_id, is_active=True)
        except FlatpakRemote.DoesNotExist:
            return JsonResponse({'error': 'Invalid remote'}, status=400)

        created = skipped = 0
        for ref in refs:
            ref = str(ref).strip()
            if not ref:
                continue
            parts = ref.split('/')
            if len(parts) >= 4:
                display_name = f"{parts[1]}/{parts[3]}"
            elif len(parts) >= 2:
                display_name = parts[1]
            else:
                display_name = ref

            ext, was_created = ExternalRef.objects.get_or_create(
                repository=repository,
                ref=ref,
                defaults={
                    'remote': remote,
                    'display_name': display_name,
                    'created_by': request.user,
                    'status': 'pending',
                },
            )
            if was_created:
                pull_external_ref_task.delay(ext.pk)
                created += 1
            else:
                skipped += 1

        return JsonResponse({
            'created': created,
            'skipped': skipped,
            'redirect_url': reverse('flatpak:external_list'),
        })


class ExternalRefDetailView(LoginRequiredMixin, DetailView):
    template_name = 'flatpak/external_detail.html'
    context_object_name = 'ext'

    def get_queryset(self):
        from .models import ExternalRef
        return ExternalRef.objects.select_related('repository', 'remote', 'created_by')


class ExternalRefCreateView(LoginRequiredMixin, CreateView):
    template_name = 'flatpak/external_form.html'

    def get_form_class(self):
        from django import forms
        from .models import ExternalRef, FlatpakRemote

        class ExternalRefForm(forms.ModelForm):
            class Meta:
                model = ExternalRef
                fields = ['repository', 'remote', 'ref']
                widgets = {
                    'ref': forms.TextInput(attrs={
                        'placeholder': 'e.g. runtime/org.kde.Platform/x86_64/5.15',
                        'class': 'form-control font-monospace',
                    }),
                }

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                # Only allow parent (top-level) repositories
                self.fields['repository'].queryset = Repository.objects.filter(
                    parent_repos__isnull=True, is_active=True
                )
                self.fields['remote'].queryset = FlatpakRemote.objects.filter(is_active=True)

        return ExternalRefForm

    def get_initial(self):
        initial = super().get_initial()
        for field in ('ref', 'remote', 'repository'):
            if field in self.request.GET:
                initial[field] = self.request.GET[field]
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        # Auto-derive display_name from ref
        ref = form.instance.ref
        parts = ref.split('/')
        if len(parts) >= 4:
            form.instance.display_name = f"{parts[1]}/{parts[3]}"
        elif len(parts) >= 2:
            form.instance.display_name = parts[1]
        response = super().form_valid(form)
        # Immediately queue the pull task so "Save & Pull" actually pulls
        from apps.flatpak.tasks import pull_external_ref_task
        pull_external_ref_task.delay(self.object.pk)
        return response

    def get_success_url(self):
        return reverse('flatpak:external_detail', kwargs={'pk': self.object.pk})


class ExternalRefDeleteView(LoginRequiredMixin, DeleteView):
    template_name = 'flatpak/external_confirm_delete.html'
    success_url = reverse_lazy('flatpak:external_list')

    def get_queryset(self):
        from .models import ExternalRef
        return ExternalRef.objects.all()

    def form_valid(self, form):
        """Remove the ref from repos before deleting the DB record."""
        from apps.flatpak.tasks import remove_external_ref_from_repos
        ext = self.get_object()
        remove_external_ref_from_repos(ext)
        return super().form_valid(form)


class ExternalRefUpdateView(LoginRequiredMixin, UpdateView):
    model = ExternalRef
    template_name = 'flatpak/external_form.html'
    fields = ['organisations']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['edit_mode'] = True
        context['all_organisations'] = Organisation.objects.all()
        if self.request.method == 'POST':
            context['selected_org_pks'] = set(self.request.POST.getlist('organisations'))
        else:
            context['selected_org_pks'] = set(str(pk) for pk in self.object.organisations.values_list('pk', flat=True))
        return context

    def form_valid(self, form):
        messages.success(self.request, f'External ref updated.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('flatpak:external_detail', kwargs={'pk': self.object.pk})


class ExternalRefPullView(LoginRequiredMixin, View):
    """Queue a pull (and subsequent publish) task for an ExternalRef."""

    def post(self, request, pk):
        from .models import ExternalRef
        from apps.flatpak.tasks import pull_external_ref_task

        ext = get_object_or_404(ExternalRef, pk=pk)
        if ext.status in ('pulling', 'publishing'):
            return JsonResponse({'error': f'Already in progress (status: {ext.status})'}, status=400)

        # Reset for re-pull
        ext.status = 'pending'
        ext.log = ''
        ext.error_message = ''
        ext.save()

        pull_external_ref_task.delay(ext.pk)
        return JsonResponse({'status': 'success', 'message': f'Pull started for {ext.display_name or ext.ref}'})


class ExternalRefPublishView(LoginRequiredMixin, View):
    """Re-publish an already-pulled ExternalRef (e.g. to a different repository after editing)."""

    def post(self, request, pk):
        from .models import ExternalRef
        from apps.flatpak.tasks import publish_external_ref_task

        ext = get_object_or_404(ExternalRef, pk=pk)
        if ext.status != 'pulled':
            return JsonResponse({'error': f'Ref must be in pulled state (current: {ext.status})'}, status=400)

        publish_external_ref_task.delay(ext.pk)
        return JsonResponse({'status': 'success', 'message': f'Publish started for {ext.display_name or ext.ref}'})


class ExternalRefStatusView(LoginRequiredMixin, View):
    """Return current status + log for an ExternalRef (polling endpoint)."""

    def get(self, request, pk):
        from .models import ExternalRef
        ext = get_object_or_404(ExternalRef, pk=pk)
        return JsonResponse({
            'status': ext.status,
            'log': ext.log,
            'commit_hash': ext.commit_hash,
            'error_message': ext.error_message,
        })


class OrganisationListView(LoginRequiredMixin, ListView):
    model = Organisation
    template_name = 'flatpak/organisation_list.html'
    context_object_name = 'organisations'


class OrganisationCreateView(LoginRequiredMixin, CreateView):
    model = Organisation
    template_name = 'flatpak/organisation_form.html'
    fields = ['name', 'responsible_name', 'responsible_email', 'description']
    success_url = reverse_lazy('flatpak:organisation_list')


class OrganisationUpdateView(LoginRequiredMixin, UpdateView):
    model = Organisation
    template_name = 'flatpak/organisation_form.html'
    fields = ['name', 'responsible_name', 'responsible_email', 'description']
    success_url = reverse_lazy('flatpak:organisation_list')


class OrganisationDeleteView(LoginRequiredMixin, DeleteView):
    model = Organisation
    template_name = 'flatpak/organisation_confirm_delete.html'
    success_url = reverse_lazy('flatpak:organisation_list')


class ClientListView(LoginRequiredMixin, ListView):
    template_name = 'flatpak/client_list.html'
    context_object_name = 'clients'

    def _annotate_status(self, clients, threshold):
        for client in clients:
            if client.last_checkin is None or client.last_checkin < threshold:
                client.status = 'red'
            elif client.outdated_count > 0 or client.foreign_count > 0:
                client.status = 'yellow'
            else:
                client.status = 'green'

    def get_queryset(self):
        import json as _json
        from .models import Client, SiteConfig
        from django.utils import timezone
        from datetime import timedelta
        stale_hours = SiteConfig.get_solo().client_stale_hours
        threshold = timezone.now() - timedelta(hours=stale_hours)
        qs = list(Client.objects.prefetch_related('organisations').all())
        self._annotate_status(qs, threshold)
        # Pre-serialize JSON so the template emits valid JSON strings.
        # Django's template renders Python lists/dicts with repr() (single
        # quotes) which JSON.parse() cannot parse.
        for client in qs:
            client.installed_json = _json.dumps(client.installed_flatpaks or [])
            client.foreign_json   = _json.dumps(client.foreign_flatpaks   or [])
            client.outdated_json  = _json.dumps(client.outdated_flatpaks  or [])
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import SiteConfig
        from django.utils import timezone
        from datetime import timedelta
        stale_hours = SiteConfig.get_solo().client_stale_hours
        threshold = timezone.now() - timedelta(hours=stale_hours)
        self._annotate_status(context['clients'], threshold)
        context['all_organisations'] = Organisation.objects.all()
        return context


class ClientDetailView(LoginRequiredMixin, DetailView):
    template_name = 'flatpak/client_detail.html'
    context_object_name = 'client'

    def get_object(self):
        from .models import Client
        return get_object_or_404(Client.objects.prefetch_related('organisations'), pk=self.kwargs['pk'])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import SiteConfig
        from django.utils import timezone
        from datetime import timedelta
        client = context['client']
        stale_hours = SiteConfig.get_solo().client_stale_hours
        threshold = timezone.now() - timedelta(hours=stale_hours)
        if client.last_checkin is None or client.last_checkin < threshold:
            client.status = 'red'
        elif client.outdated_count > 0 or client.foreign_count > 0:
            client.status = 'yellow'
        else:
            client.status = 'green'

        # Build a single annotated list: status = 'uptodate' | 'outdated' | 'foreign'
        outdated_map = {pkg['app_id']: pkg for pkg in (client.outdated_flatpaks or [])}
        managed = set(client.managed_remotes or [])

        # Map package_id → Package pk for all installed apps (enables detail links)
        installed_ids = [p['app_id'] for p in (client.installed_flatpaks or [])]
        pkg_pk_map = {
            p.package_id: p.pk
            for p in Package.objects.filter(package_id__in=installed_ids).only('package_id', 'pk')
        }

        order = {'outdated': 0, 'foreign': 1, 'uptodate': 2}
        annotated = []
        for pkg in (client.installed_flatpaks or []):
            entry = dict(pkg)
            entry['installed_by'] = pkg.get('user', 'system')
            if pkg.get('origin') not in managed:
                entry['pkg_status'] = 'foreign'
            elif pkg['app_id'] in outdated_map:
                entry['pkg_status'] = 'outdated'
                entry['new_version'] = outdated_map[pkg['app_id']].get('new_version', '')
            else:
                entry['pkg_status'] = 'uptodate'
            if entry['pkg_status'] != 'foreign' and pkg['app_id'] in pkg_pk_map:
                entry['pkg_pk'] = pkg_pk_map[pkg['app_id']]
            annotated.append(entry)
        annotated.sort(key=lambda p: (
            order[p['pkg_status']],
            (p.get('name') or p['app_id']).lower(),
        ))
        context['installed_annotated'] = annotated
        context['all_organisations'] = Organisation.objects.all()
        context['selected_org_pks'] = set(str(pk) for pk in client.organisations.values_list('pk', flat=True))
        return context


class ClientAssignOrgsView(LoginRequiredMixin, View):
    """POST — assign organisations to a single client."""

    def post(self, request, pk):
        from .models import Client
        client = get_object_or_404(Client, pk=pk)
        org_pks = request.POST.getlist('organisations')
        client.organisations.set(Organisation.objects.filter(pk__in=org_pks))
        messages.success(request, f'Organisations updated for {client.hostname}.')
        return redirect('flatpak:client_detail', pk=pk)


class ClientBulkActionView(LoginRequiredMixin, View):
    """POST — bulk assign organisations or bulk delete clients."""

    def post(self, request):
        import json as _json
        from .models import Client
        try:
            data = _json.loads(request.body)
        except (ValueError, _json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        action = data.get('action')
        client_pks = data.get('client_pks', [])
        if not client_pks:
            return JsonResponse({'error': 'No clients selected'}, status=400)

        clients = Client.objects.filter(pk__in=client_pks)

        if action == 'assign_orgs':
            org_pks = data.get('org_pks', [])
            orgs = Organisation.objects.filter(pk__in=org_pks)
            for client in clients:
                client.organisations.set(orgs)
            return JsonResponse({'status': 'ok', 'updated': clients.count()})

        if action == 'delete':
            count = clients.count()
            clients.delete()
            return JsonResponse({'status': 'ok', 'deleted': count})

        return JsonResponse({'error': f'Unknown action: {action}'}, status=400)


import json
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator


@method_decorator(csrf_exempt, name='dispatch')
class ClientCheckinView(View):
    """
    POST /api/client-checkin/
    No authentication required. Accepts JSON from the flat-manager-checkin agent.
    Creates or updates the Client record for the reporting host.
    """
    def post(self, request):
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        hostname = data.get('hostname', '').strip()
        if not hostname:
            return JsonResponse({'error': 'hostname required'}, status=400)

        from .models import Client
        from django.utils import timezone

        remotes = data.get('remotes', [])
        managed_remote_names = data.get('managed_remotes', [])
        installed = data.get('installed', [])
        user_flatpaks = data.get('user_flatpaks', [])

        # Compute foreign flatpaks
        managed_set = set(managed_remote_names)
        foreign_flatpaks = [
            pkg for pkg in installed
            if pkg.get('origin') not in managed_set
        ]

        # Compute outdated flatpaks server-side: compare each installed package
        # from a managed remote against the latest published version in our DB.
        managed_app_ids = list({
            p['app_id'] for p in installed if p.get('origin') in managed_set
        })
        latest_versions = {}
        if managed_app_ids:
            from .models import Package as _Package
            for p in _Package.objects.filter(
                package_id__in=managed_app_ids,
                status='published',
            ).only('package_id', 'version'):
                existing = latest_versions.get(p.package_id)
                if existing is None:
                    latest_versions[p.package_id] = p.version
                else:
                    try:
                        from packaging.version import Version as _PV
                        if _PV(p.version) > _PV(existing):
                            latest_versions[p.package_id] = p.version
                    except Exception:
                        pass

        try:
            from packaging.version import Version as _PkgVer, InvalidVersion as _BadVer
            def _ver_lt(a, b):
                try:
                    return _PkgVer(a) < _PkgVer(b)
                except _BadVer:
                    return a != b
        except ImportError:
            def _ver_lt(a, b):
                return a != b

        outdated_flatpaks = []
        for pkg in installed:
            if pkg.get('origin') not in managed_set:
                continue
            app_id = pkg['app_id']
            inst_ver = pkg.get('version', '')
            latest_ver = latest_versions.get(app_id, '')
            if not inst_ver or not latest_ver:
                continue
            if _ver_lt(inst_ver, latest_ver):
                outdated_flatpaks.append({
                    'app_id':          app_id,
                    'current_version': inst_ver,
                    'new_version':     latest_ver,
                    'origin':          pkg.get('origin', ''),
                    'name':            pkg.get('name', ''),
                })

        client, _ = Client.objects.get_or_create(hostname=hostname)
        client.last_checkin = timezone.now()
        client.remotes = remotes
        client.managed_remotes = managed_remote_names
        client.installed_flatpaks = installed
        client.installed_count = len(installed)
        client.foreign_flatpaks = foreign_flatpaks
        client.foreign_count = len(foreign_flatpaks)
        client.outdated_flatpaks = outdated_flatpaks
        client.outdated_count = len(outdated_flatpaks)
        client.user_flatpaks = user_flatpaks
        client.save()

        # Notify the clients page in real-time so it can update without reload.
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    'notifications',
                    {
                        'type': 'notification_message',
                        'notification_type': 'client_updated',
                        'pk': client.pk,
                        'hostname': hostname,
                        'installed_count': client.installed_count,
                        'foreign_count': client.foreign_count,
                        'outdated_count': client.outdated_count,
                        'installed_flatpaks': installed,
                        'foreign_flatpaks': foreign_flatpaks,
                        'outdated_flatpaks': outdated_flatpaks,
                        'last_checkin': client.last_checkin.strftime('%b %d, %H:%M') if client.last_checkin else '',
                    }
                )
        except Exception:
            pass  # WS push is best-effort; checkin must still succeed

        return JsonResponse({'status': 'ok', 'hostname': hostname})


# ─── BuildStream Source views ─────────────────────────────────────────────────

class BuildStreamSourceListView(LoginRequiredMixin, ListView):
    model = BuildStreamSource
    template_name = 'flatpak/buildstreamsource_list.html'
    context_object_name = 'sources'
    ordering = ['-created_at']

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['all_organisations'] = Organisation.objects.all()
        return ctx


class BuildStreamBulkActionView(LoginRequiredMixin, View):
    """POST — bulk rebuild, assign organisations, or delete BST sources."""

    def post(self, request):
        import json as _json
        try:
            data = _json.loads(request.body)
        except (ValueError, _json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        action = data.get('action')
        ids = data.get('ids', [])
        if not isinstance(ids, list) or not ids:
            return JsonResponse({'error': 'No sources selected'}, status=400)

        sources = BuildStreamSource.objects.filter(pk__in=ids)

        if action == 'assign_orgs':
            org_pks = data.get('org_pks', [])
            orgs = Organisation.objects.filter(pk__in=org_pks)
            for source in sources:
                source.organisations.set(orgs)
            return JsonResponse({'status': 'success', 'updated': sources.count()})

        if action == 'rebuild':
            count = 0
            for source in sources:
                if source.status in ('failed', 'built', 'published', 'cancelled'):
                    source.status = 'pending'
                    source.build_number += 1
                    source.error_message = ''
                    source.save()
                    count += 1
            return JsonResponse({'status': 'success', 'queued': count})

        if action == 'delete':
            count = sources.count()
            sources.delete()
            return JsonResponse({'status': 'success', 'deleted': count})

        return JsonResponse({'error': f'Unknown action: {action}'}, status=400)


class BuildStreamSourceCreateView(LoginRequiredMixin, CreateView):
    model = BuildStreamSource
    template_name = 'flatpak/buildstreamsource_form.html'
    fields = ['repository', 'name', 'git_repo_url', 'git_branch', 'bst_element', 'bst_version', 'organisations']

    def get_initial(self):
        initial = super().get_initial()
        for field in ('name', 'git_repo_url', 'git_branch', 'bst_element'):
            if field in self.request.GET:
                initial[field] = self.request.GET[field]
        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['repository'].queryset = Repository.objects.filter(parent_repos__isnull=True)
        form.fields['repository'].help_text = "Only repositories without parent repos can have builds"
        return form

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(
            self.request,
            f'BuildStream source \u201c{form.instance.name}\u201d created. '
            'The build will start automatically.'
        )
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['all_organisations'] = Organisation.objects.all()
        context['selected_org_pks'] = set(self.request.POST.getlist('organisations')) if self.request.method == 'POST' else set()
        return context

    def get_success_url(self):
        return reverse('flatpak:bst_source_detail', kwargs={'pk': self.object.pk})


class BuildStreamSourceDetailView(LoginRequiredMixin, DetailView):
    model = BuildStreamSource
    template_name = 'flatpak/buildstreamsource_detail.html'
    context_object_name = 'source'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['builds'] = self.object.builds.order_by('-build_number')[:20]
        # Latest published build for promote UI
        latest_published = self.object.builds.filter(status='published').order_by('-build_number').first()
        context['latest_published_build'] = latest_published
        context['available_bst_targets'] = (
            get_available_bst_promotion_targets(latest_published)
            if latest_published else []
        )
        from apps.flatpak.models import BstPromotion
        context['bst_promotions'] = (
            BstPromotion.objects
            .filter(bst_source=self.object)
            .select_related('build', 'target_repo', 'promoted_by')
            .order_by('-created_at')[:20]
        )
        # Build a coverage map: ref -> {'kind': 'bst'|'package', 'pk': int, 'name': str}
        # Used by the template to show whether each produced ref is already managed elsewhere.
        ref_coverage = {}
        for bst in BuildStreamSource.objects.exclude(pk=self.object.pk).only('pk', 'name', 'produced_refs'):
            for raw in bst.produced_refs.splitlines():
                r = raw.strip()
                if r and r not in ref_coverage:
                    ref_coverage[r] = {'kind': 'bst', 'pk': bst.pk, 'name': bst.name}
        for pkg in Package.objects.all().only('pk', 'package_id', 'package_name', 'arch', 'branch'):
            disp = pkg.package_name or pkg.package_id
            for prefix in ('app', 'runtime', 'appstream'):
                key = f"{prefix}/{pkg.package_id}/{pkg.arch}/{pkg.branch}"
                if key not in ref_coverage:
                    ref_coverage[key] = {'kind': 'package', 'pk': pkg.pk, 'name': disp}

        # Parse produced refs into grouped sections with per-ref coverage info.
        # Refs look like: runtime/org.freedesktop.Sdk/x86_64/24.08
        produced = [r for r in self.object.produced_refs.splitlines() if r.strip()]
        grouped = {}
        for ref in sorted(produced):
            bucket = ref.split('/')[0] if '/' in ref else 'other'
            grouped.setdefault(bucket, []).append({
                'ref': ref,
                'coverage': ref_coverage.get(ref),
            })
        context['produced_refs'] = produced
        context['produced_refs_grouped'] = grouped
        return context


class BuildStreamSourceUpdateView(LoginRequiredMixin, UpdateView):
    model = BuildStreamSource
    template_name = 'flatpak/buildstreamsource_form.html'
    fields = ['repository', 'name', 'git_repo_url', 'git_branch', 'bst_element', 'bst_version', 'organisations']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['edit_mode'] = True
        context['all_organisations'] = Organisation.objects.all()
        if self.request.method == 'POST':
            context['selected_org_pks'] = set(self.request.POST.getlist('organisations'))
        else:
            context['selected_org_pks'] = set(str(pk) for pk in self.object.organisations.values_list('pk', flat=True))
        return context

    def form_valid(self, form):
        messages.success(self.request, f'BuildStream source \u201c{self.object.name}\u201d updated.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('flatpak:bst_source_detail', kwargs={'pk': self.object.pk})


class BuildStreamSourceDeleteView(LoginRequiredMixin, DeleteView):
    model = BuildStreamSource
    template_name = 'flatpak/buildstreamsource_confirm_delete.html'
    context_object_name = 'source'
    success_url = reverse_lazy('flatpak:bst_source_list')


class BuildStreamSourceRetryView(LoginRequiredMixin, View):
    """Reset a failed/built BST source to pending so it is picked up again."""

    def post(self, request, pk):
        source = get_object_or_404(BuildStreamSource, pk=pk)
        if source.status in ('failed', 'built', 'published', 'cancelled'):
            source.status = 'pending'
            source.build_number += 1
            source.error_message = ''
            source.save()
            messages.success(request, f'Build queued for \u201c{source.name}\u201d.')
        else:
            messages.warning(request, f'Cannot retry: current status is {source.status}.')
        return redirect('flatpak:bst_source_detail', pk=pk)


class BuildStreamSourceForceRebuildView(LoginRequiredMixin, View):
    """Clear the BST artifact cache for a source and trigger a full rebuild from source."""

    ALLOWED_STATUSES = {'failed', 'built', 'published', 'cancelled'}

    def post(self, request, pk):
        source = get_object_or_404(BuildStreamSource, pk=pk)
        if source.status not in self.ALLOWED_STATUSES:
            return JsonResponse(
                {'error': f'Cannot force-rebuild: current status is \'{source.status}\''},
                status=400,
            )
        source.status = 'pending'
        source.build_number += 1
        source.error_message = ''
        source.save()
        from apps.flatpak.tasks import buildstream_build_task
        buildstream_build_task.delay(source.pk, force_rebuild=True)
        return JsonResponse({'status': 'ok', 'build_number': source.build_number})


class BuildStreamIntegrityCheckView(LoginRequiredMixin, View):
    """Run ostree fsck on build-repo and return corruption status as JSON."""

    def get(self, request):
        import re as _re
        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        if not os.path.exists(os.path.join(build_repo_path, 'config')):
            return JsonResponse({'status': 'error', 'message': 'build-repo does not exist'}, status=400)

        try:
            fsck = subprocess.run(
                ['ostree', 'fsck', '--repo', build_repo_path],
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            return JsonResponse({'status': 'error', 'message': 'ostree fsck timed out after 300s'}, status=500)

        if fsck.returncode == 0:
            return JsonResponse({'status': 'clean', 'corrupted_refs': [], 'corrupted_objects': []})

        # Parse corrupted commit hashes and object hashes from fsck output.
        # Error format: "In commits <hash1>, <hash2>, ...: fsck content object <obj>: ..."
        output = fsck.stderr + '\n' + fsck.stdout
        corrupted_commits = set()
        corrupted_objects = set()
        for line in output.splitlines():
            if 'In commits' in line:
                # Extract hex64 strings from the "In commits ..." part only
                before_fsck = line.split(': fsck')[0] if ': fsck' in line else line
                for h in _re.findall(r'[0-9a-f]{64}', before_fsck):
                    corrupted_commits.add(h)
            obj_m = _re.search(r'content object ([0-9a-f]{64})', line)
            if obj_m:
                corrupted_objects.add(obj_m.group(1))

        # fsck can exit non-zero for reasons other than live corruption
        # (e.g. "N partial commits not verified" after objects were previously
        # deleted by fsck --delete).  If we parsed zero corrupted commits the
        # repo is effectively clean.
        if not corrupted_commits:
            return JsonResponse({
                'status': 'clean',
                'corrupted_refs': [],
                'corrupted_objects': [],
                'note': 'fsck reported partial commits (from prior cleanup) but no active corruption.',
            })

        # Map corrupted commits → active refs in build-repo
        corrupted_refs = []
        try:
            refs_result = subprocess.run(
                ['ostree', 'refs', f'--repo={build_repo_path}'],
                capture_output=True, text=True, timeout=30,
            )
            if refs_result.returncode == 0:
                for ref in (r.strip() for r in refs_result.stdout.splitlines() if r.strip()):
                    rev = subprocess.run(
                        ['ostree', 'rev-parse', f'--repo={build_repo_path}', ref],
                        capture_output=True, text=True, timeout=10,
                    )
                    if rev.returncode == 0:
                        commit = rev.stdout.strip()
                        if commit in corrupted_commits:
                            corrupted_refs.append({'ref': ref, 'commit': commit[:16]})
        except Exception:
            pass

        return JsonResponse({
            'status': 'corrupted',
            'corrupted_refs': corrupted_refs,
            'corrupted_objects': sorted(corrupted_objects),
            'corrupted_commit_count': len(corrupted_commits),
        })
