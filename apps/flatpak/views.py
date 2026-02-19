from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.views import View
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from .models import GPGKey, Repository, RepositorySubset, Package, Build, Promotion
from .forms import GPGKeyGenerateForm, GPGKeyImportForm
from .utils.gpg import generate_gpg_key, import_gpg_key
from .utils.ostree import init_ostree_repo, sign_repo_summary, delete_ostree_repo, temp_gpg_homedir, update_repo_metadata

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
        from django.db.models import Q
        qs = Package.objects.select_related('repository').order_by('-created_at')
        q = self.request.GET.get('q', '').strip()
        status = self.request.GET.get('status', '').strip()
        repo = self.request.GET.get('repo', '').strip()
        if q:
            qs = qs.filter(Q(package_name__icontains=q) | Q(package_id__icontains=q))
        if status:
            qs = qs.filter(status=status)
        if repo:
            qs = qs.filter(repository_id=repo)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['repositories'] = Repository.objects.filter(is_active=True)
        ctx['status_choices'] = Package.STATUS_CHOICES
        ctx['filter_q'] = self.request.GET.get('q', '')
        ctx['filter_status'] = self.request.GET.get('status', '')
        ctx['filter_repo'] = self.request.GET.get('repo', '')
        get_params = self.request.GET.copy()
        get_params.pop('page', None)
        ctx['filter_params'] = get_params.urlencode()
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
        from apps.flatpak.tasks import _fetch_latest_upstream_tag
        package = get_object_or_404(Package, pk=pk)
        if not package.upstream_url:
            return JsonResponse({'error': 'No upstream URL configured for this package'}, status=400)
        version, error = _fetch_latest_upstream_tag(package.upstream_url)
        if error is not None:
            return JsonResponse({'error': error}, status=502)
        package.upstream_version = version
        package.upstream_checked_at = tz.now()
        package.save(update_fields=['upstream_version', 'upstream_checked_at'])
        return JsonResponse({
            'version': version,
            'has_update': bool(package.version and version and version != package.version),
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


class BuildListView(LoginRequiredMixin, ListView):
    """List all builds across all packages with optional filtering."""
    model = Build
    template_name = 'flatpak/build_list.html'
    context_object_name = 'builds'
    paginate_by = 50

    def get_queryset(self):
        from django.db.models import Q
        qs = Build.objects.select_related('package', 'package__repository').order_by('-started_at')
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
            qs = qs.filter(package__repository_id=repo)
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
            if self.object.status == 'published' else []
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


class PromotionDeleteView(LoginRequiredMixin, View):
    """Delete a promotion and remove the OSTree ref from the target repo."""

    def post(self, request, pk):
        import os as _os
        import subprocess as _sp
        promotion = get_object_or_404(Promotion, pk=pk)
        if promotion.status == 'promoted':
            target_repo_path = _os.path.join(settings.REPOS_BASE_PATH, promotion.target_repo.name)
            ref_name = (
                f'app/{promotion.package.package_id}'
                f'/{promotion.package.arch}/{promotion.package.branch}'
            )
            try:
                _sp.run(
                    ['ostree', 'refs', '--delete', ref_name, f'--repo={target_repo_path}'],
                    capture_output=True, text=True, timeout=60
                )
                _sp.run(
                    ['ostree', 'summary', '-u', f'--repo={target_repo_path}'],
                    capture_output=True, text=True, timeout=60
                )
                if promotion.target_repo.gpg_key:
                    with temp_gpg_homedir(promotion.target_repo.gpg_key) as homedir:
                        sign_repo_summary(
                            target_repo_path,
                            promotion.target_repo.gpg_key.key_id,
                            gpg_homedir=homedir,
                        )
            except Exception as e:
                return JsonResponse({'error': f'Failed to remove ref from repo: {e}'}, status=500)
        promotion.delete()
        return JsonResponse({'status': 'ok'})


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
            Build.objects.filter(status='published')
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
        context['repositories'] = Repository.objects.filter(is_active=True)
        context['promo_status_choices'] = Promotion.STATUS_CHOICES
        context['filter_q'] = self.request.GET.get('q', '')
        context['filter_status'] = self.request.GET.get('status', '')
        context['filter_repo'] = self.request.GET.get('repo', '')
        context['filter_pub_repo'] = self.request.GET.get('pub_repo', '')
        get_params = self.request.GET.copy()
        get_params.pop('page', None)
        context['filter_params'] = get_params.urlencode()
        return context


class PackageCreateView(LoginRequiredMixin, CreateView):
    """Create new package."""
    model = Package
    template_name = 'flatpak/package_form.html'
    fields = ['repository', 'package_id', 'package_name', 'version', 'git_repo_url', 'git_branch', 'upstream_url', 'branch', 'arch', 'installation_type']
    
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
        
        # Package will be automatically picked up by the periodic check_pending_builds task
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
    fields = ['repository', 'package_id', 'package_name', 'version', 'branch', 'arch', 'git_repo_url', 'git_branch', 'upstream_url', 'installation_type']
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['edit_mode'] = True
        context['package'] = self.object
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
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        package = self.get_object()
        context['can_cancel'] = package.status in ['pending', 'building']
        context['can_delete'] = package.status in ['failed', 'cancelled', 'published']
        return context
    
    def post(self, request, *args, **kwargs):
        """Override post to handle cancellation vs deletion."""
        import sys
        print(f"!!!!! POST METHOD CALLED !!!!!", file=sys.stderr, flush=True)
        
        # Call delete which has our custom logic
        return self.delete(request, *args, **kwargs)
    
    def delete(self, request, *args, **kwargs):
        import logging
        import json
        import sys
        logger = logging.getLogger(__name__)
        
        package = self.get_object()
        package_id = package.package_id
        current_status = package.status
        
        # Force output to console
        print(f"!!!!! DELETE METHOD CALLED: package {package_id} (pk={package.pk}) status={current_status} !!!!!", file=sys.stderr, flush=True)
        logger.info(f"Delete method called for package {package_id} (pk={package.pk}) with status {current_status} by user {request.user.username}")
        
        # If package is in progress, just cancel it (don't delete)
        if current_status in ['pending', 'building', 'committing', 'publishing']:
            print(f"!!!!! CANCELLING (NOT DELETING) package {package_id} !!!!!", file=sys.stderr, flush=True)
            package.status = 'cancelled'
            package.save()
            print(f"!!!!! Status saved as cancelled for package {package_id} !!!!!", file=sys.stderr, flush=True)
            
            # Note: BuildLog will be created when we start tracking Build history
            
            logger.info(f"Package {package_id} (pk={package.pk}) cancelled successfully (status changed from {current_status} to cancelled, NOT DELETED)")
            messages.success(request, f'Package {package_id} has been cancelled (status changed from {current_status} to cancelled).')
            
            # Handle AJAX requests
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json':
                from django.http import JsonResponse
                return JsonResponse({'status': 'cancelled', 'message': f'Package {package_id} cancelled'})
            
            print(f"!!!!! Returning redirect, package {package_id} should NOT be deleted !!!!!", file=sys.stderr, flush=True)
            return HttpResponseRedirect(self.success_url)
        
        # Only delete if package is in a terminal state
        if current_status not in ['failed', 'cancelled', 'published']:
            logger.warning(f"Attempted to delete package {package_id} (pk={package.pk}) with invalid status {current_status}")
            messages.error(request, f'Cannot delete package with status: {package.status}')
            return HttpResponseRedirect(reverse('flatpak:package_detail', kwargs={'pk': package.pk}))
        
        # Actually delete the package
        logger.info(f"Deleting package {package_id} (pk={package.pk}) from database (status was {current_status})")
        messages.success(request, f'Package {package_id} deleted successfully.')
        # TODO: Clean up build artifacts from build-repo
        return super().delete(request, *args, **kwargs)


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


class ConfigView(LoginRequiredMixin, View):
    """Display and update site-wide configuration."""

    def get(self, request):
        from .forms import SiteConfigForm
        from .models import SiteConfig
        form = SiteConfigForm(instance=SiteConfig.get_solo())
        return render(request, 'flatpak/config.html', {'form': form})

    def post(self, request):
        from .forms import SiteConfigForm
        from .models import SiteConfig
        form = SiteConfigForm(request.POST, instance=SiteConfig.get_solo())
        if form.is_valid():
            form.save()
            messages.success(request, 'Configuration saved successfully.')
        return render(request, 'flatpak/config.html', {'form': form})


class RunCleanupNowView(LoginRequiredMixin, View):
    """Trigger cleanup_failed_builds task immediately (synchronous, not via queue)."""

    def post(self, request):
        from apps.flatpak.tasks import cleanup_failed_builds
        result = cleanup_failed_builds()  # run synchronously
        return JsonResponse({'status': 'ok', 'message': result})


@login_required
def dependencies_list(request):
    """List all installed Flatpak dependencies (SDKs, runtimes, extensions)."""
    import subprocess
    import re
    
    dependencies = {
        'system': [],
        'user': [],
        'errors': []
    }
    
    # Get system installations
    try:
        result = subprocess.run(
            ['flatpak', 'list', '--system', '--columns=name,application,version,branch,origin'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split('\t')
                    if len(parts) >= 5:
                        dependencies['system'].append({
                            'name': parts[0],
                            'id': parts[1],
                            'version': parts[2],
                            'branch': parts[3],
                            'origin': parts[4],
                            'type': 'SDK' if 'Sdk' in parts[1] else 'Runtime' if 'Platform' in parts[1] or 'runtime' in parts[1].lower() else 'Extension' if 'Extension' in parts[1] else 'App'
                        })
    except subprocess.TimeoutExpired:
        dependencies['errors'].append('Timeout listing system flatpaks')
    except Exception as e:
        dependencies['errors'].append(f'Error listing system flatpaks: {str(e)}')
    
    # Get user installations
    try:
        result = subprocess.run(
            ['flatpak', 'list', '--user', '--columns=name,application,version,branch,origin'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split('\t')
                    if len(parts) >= 5:
                        dependencies['user'].append({
                            'name': parts[0],
                            'id': parts[1],
                            'version': parts[2],
                            'branch': parts[3],
                            'origin': parts[4],
                            'type': 'SDK' if 'Sdk' in parts[1] else 'Runtime' if 'Platform' in parts[1] or 'runtime' in parts[1].lower() else 'Extension' if 'Extension' in parts[1] else 'App'
                        })
    except subprocess.TimeoutExpired:
        dependencies['errors'].append('Timeout listing user flatpaks')
    except Exception as e:
        dependencies['errors'].append(f'Error listing user flatpaks: {str(e)}')
    
    # Filter to only show SDKs, Runtimes, and Extensions (not apps)
    dependencies['system'] = [d for d in dependencies['system'] if d['type'] in ['SDK', 'Runtime', 'Extension']]
    dependencies['user'] = [d for d in dependencies['user'] if d['type'] in ['SDK', 'Runtime', 'Extension']]
    
    # Sort by type, then name
    dependencies['system'].sort(key=lambda x: (x['type'], x['name']))
    dependencies['user'].sort(key=lambda x: (x['type'], x['name']))
    
    context = {
        'dependencies': dependencies,
        'total_system': len(dependencies['system']),
        'total_user': len(dependencies['user']),
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
