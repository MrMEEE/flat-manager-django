from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from .permissions import IsAdmin, CanBuild, CanRepoAdmin
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from django.db.models import Q
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.users.models import User, UserProfile, APIToken
from apps.users.mixins import scope_queryset_for_user, apply_default_organisation
from apps.flatpak.models import GPGKey, Repository, RepositorySubset, Package, Build, BuildArtifact, BuildLog, Token
from apps.flatpak.models import ExternalRef, ExternalRefPromotion, Promotion
from apps.flatpak.views import (
    _has_scoped_permission, _scope_published_queryset,
    get_available_promotion_targets, get_available_external_ref_promotion_targets,
)
from apps.flatpak.utils.gpg import generate_gpg_key, import_gpg_key
from .serializers import (
    UserSerializer, UserProfileSerializer, APITokenSerializer,
    GPGKeySerializer, GPGKeyListSerializer, RepositorySerializer, RepositorySubsetSerializer,
    PackageSerializer, BuildSerializer, BuildArtifactSerializer, BuildLogSerializer, TokenSerializer
)
from .serializers import (
    PromotionRequestSerializer, ExternalRefSerializer, PromotionSerializer,
    ExternalRefPromotionSerializer,
)


class UserViewSet(viewsets.ModelViewSet):
    """
    API endpoint for users.
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated()]
        return [IsAdmin()]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering_fields = ['username', 'created_at']
    
    @action(detail=False, methods=['get'])
    def me(self, request):
        """Get current user profile."""
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)


class UserProfileViewSet(viewsets.ModelViewSet):
    """
    API endpoint for user profiles.
    """
    queryset = UserProfile.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated()]
        return [IsAdmin()]


class GPGKeyViewSet(viewsets.ModelViewSet):
    """
    API endpoint for GPG keys.
    """
    queryset = GPGKey.objects.all()
    serializer_class = GPGKeySerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated()]
        return [CanRepoAdmin()]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'email', 'key_id', 'fingerprint']
    ordering_fields = ['name', 'created_at']

    def get_queryset(self):
        return scope_queryset_for_user(super().get_queryset(), self.request.user, 'gpg_keys')
    
    def get_serializer_class(self):
        if self.action == 'list':
            return GPGKeyListSerializer
        return GPGKeySerializer
    
    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        apply_default_organisation(instance, self.request.user)
    
    @action(detail=True, methods=['get'])
    def public_key(self, request, pk=None):
        """Download public key only."""
        gpg_key = self.get_object()
        return Response({
            'name': gpg_key.name,
            'key_id': gpg_key.key_id,
            'fingerprint': gpg_key.fingerprint,
            'public_key': gpg_key.public_key
        })
    
    @action(detail=False, methods=['post'])
    def generate(self, request):
        """Generate a new GPG key pair."""
        name = request.data.get('name')
        email = request.data.get('email')
        comment = request.data.get('comment', '')
        key_length = int(request.data.get('key_length', 4096))
        
        if not name or not email:
            return Response(
                {'error': 'name and email are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Generate key without passphrase for automated signing
            key_data = generate_gpg_key(
                name=name,
                email=email,
                passphrase=None,
                key_length=key_length,
                comment=comment
            )
            
            gpg_key = GPGKey.objects.create(
                name=name,
                email=email,
                key_id=key_data['key_id'],
                fingerprint=key_data['fingerprint'],
                public_key=key_data['public_key'],
                private_key=key_data['private_key'],
                passphrase_hint='',
                created_by=request.user
            )
            apply_default_organisation(gpg_key, request.user)
            
            serializer = self.get_serializer(gpg_key)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def import_key(self, request):
        """Import an existing GPG key."""
        name = request.data.get('name')
        email = request.data.get('email')
        public_key = request.data.get('public_key')
        private_key = request.data.get('private_key', '')
        passphrase = request.data.get('passphrase')
        
        if not name or not email or not public_key:
            return Response(
                {'error': 'name, email, and public_key are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Import key with passphrase for decryption if provided
            key_info = import_gpg_key(public_key, private_key, passphrase)
            
            gpg_key = GPGKey.objects.create(
                name=name,
                email=email,
                key_id=key_info['key_id'],
                fingerprint=key_info['fingerprint'],
                public_key=public_key,
                private_key=private_key,
                passphrase_hint='',
                created_by=request.user
            )
            apply_default_organisation(gpg_key, request.user)
            
            serializer = self.get_serializer(gpg_key)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class RepositoryViewSet(viewsets.ModelViewSet):
    """
    API endpoint for repositories.
    """
    queryset = Repository.objects.all()
    serializer_class = RepositorySerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated()]
        return [CanRepoAdmin()]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active', 'collection_id']
    search_fields = ['name', 'description', 'collection_id']
    ordering_fields = ['name', 'created_at']

    def get_queryset(self):
        return scope_queryset_for_user(super().get_queryset(), self.request.user, 'repositories')
    
    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        apply_default_organisation(instance, self.request.user)
    
    @action(detail=True, methods=['get'])
    def builds(self, request, pk=None):
        """Get all builds for a repository."""
        repository = self.get_object()
        builds = Build.objects.filter(
            Q(package__repository=repository) | Q(bst_source__repository=repository)
        ).distinct()
        serializer = BuildSerializer(builds, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def subsets(self, request, pk=None):
        """Get all subsets for a repository."""
        repository = self.get_object()
        subsets = repository.subsets.all()
        serializer = RepositorySubsetSerializer(subsets, many=True)
        return Response(serializer.data)


class RepositorySubsetViewSet(viewsets.ModelViewSet):
    """
    API endpoint for repository subsets.
    """
    queryset = RepositorySubset.objects.all()
    serializer_class = RepositorySubsetSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated()]
        return [CanRepoAdmin()]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['repository']
    search_fields = ['name', 'collection_id']


class PackageViewSet(viewsets.ModelViewSet):
    """
    API endpoint for packages (main configuration records).
    """
    queryset = Package.objects.all()
    serializer_class = PackageSerializer
    permission_classes = [CanBuild]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'repository', 'arch', 'branch']
    search_fields = ['package_id', 'package_name']
    ordering_fields = ['created_at', 'build_number']

    def get_queryset(self):
        return scope_queryset_for_user(super().get_queryset(), self.request.user, 'flatpaks')
    
    def get_permissions(self):
        """Allow unauthenticated access to logs; read-only for authenticated; write requires CanBuild."""
        if self.action in ['logs']:
            return [AllowAny()]
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated()]
        return [CanBuild()]
    
    def perform_create(self, serializer):
        package = serializer.save(created_by=self.request.user)
        apply_default_organisation(package, self.request.user)
        # Package will be automatically picked up by periodic check_pending_builds task
        # No need to manually trigger here
    
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """Start a build (manual trigger)."""
        package = self.get_object()
        if package.status != 'pending':
            return Response(
                {'error': 'Package can only be started from pending state'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Manually trigger git-based build
        if package.git_repo_url:
            from apps.flatpak.tasks import package_from_git_task
            package_from_git_task.delay(package.id)
            return Response({'status': 'Build started manually', 'package_id': package.package_id})
        else:
            return Response(
                {'error': 'Upload-based builds start automatically on first upload'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a build."""
        package = self.get_object()
        if package.status in ['published', 'failed', 'cancelled']:
            return Response(
                {'error': 'Build cannot be cancelled in current state'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        package.status = 'cancelled'
        package.save()
        return Response({'status': 'Build cancelled'})
    
    @action(detail=True, methods=['get'], authentication_classes=[], permission_classes=[AllowAny])
    def logs(self, request, pk=None):
        """Get the latest build's logs with live updates (public endpoint)."""
        # Queried directly to bypass the permission check in get_object().
        try:
            package = Package.objects.get(pk=pk)
        except Package.DoesNotExist:
            return Response({'error': 'Package not found'}, status=status.HTTP_404_NOT_FOUND)

        build = package.builds.order_by('-build_number').first()
        if build is None:
            return Response({
                'build_id': None,
                'status': package.status,
                'logs': [],
                'total_logs': 0
            })

        logs = build.logs.all().order_by('timestamp')
        
        log_data = [{
            'id': log.id,
            'message': log.message,
            'level': log.level,
            'timestamp': log.timestamp.strftime('%H:%M:%S')
        } for log in logs]
        
        return Response({
            'build_id': build.id,
            'status': build.status,
            'logs': log_data,
            'total_logs': len(log_data)
        })
    
    @action(detail=True, methods=['post'])
    def commit(self, request, pk=None):
        """Commit a build (flat-manager API compatibility)."""
        package = self.get_object()
        if package.status not in ['pending', 'building', 'built']:
            return Response(
                {'error': 'Build must be in pending, building, or built state'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Queue commit task
        from apps.flatpak.tasks import commit_package_task
        commit_package_task.delay(package.id)
        
        return Response({
            'status': 'Build commit started',
            'build_id': package.package_id
        })
    
    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        """Publish a build."""
        package = self.get_object()
        if package.status != 'committed':
            return Response(
                {'error': 'Only committed builds can be published'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        from apps.flatpak.tasks import publish_package_task
        publish_package_task.delay(package.id)
        return Response({
            'status': 'Build publishing started',
            'build_id': package.package_id
        })
    
    @action(detail=True, methods=['post'])
    def upload(self, request, pk=None):
        """Upload build artifacts (flat-manager API compatibility)."""
        package = self.get_object()
        if package.status not in ['pending', 'building']:
            return Response(
                {'error': 'Build must be in pending or building state for uploads'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # TODO: Implement file upload handling
        return Response({
            'status': 'Upload endpoint ready',
            'build_id': package.package_id
        })
    
    @action(detail=True, methods=['get', 'post'])
    def missing_objects(self, request, pk=None):
        """Check for missing OSTree objects (flat-manager API compatibility)."""
        package = self.get_object()
        
        if request.method == 'POST':
            wanted = request.data.get('wanted', [])
            # TODO: Implement OSTree object checking
            return Response({
                'missing': []  # Return list of missing objects
            })
        
        return Response({
            'build_id': package.package_id,
            'status': 'Ready for object checking'
        })
    
    @action(detail=True, methods=['post'])
    def build_ref(self, request, pk=None):
        """Create a build ref (flat-manager API compatibility)."""
        package = self.get_object()
        ref_name = request.data.get('ref')
        commit = request.data.get('commit')
        
        if not ref_name or not commit:
            return Response(
                {'error': 'ref and commit are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # TODO: Store build ref information
        return Response({
            'build_id': package.package_id,
            'ref': ref_name,
            'commit': commit
        })


class BuildViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for build history records (read-only).
    """
    queryset = Build.objects.all()
    serializer_class = BuildSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['package', 'status', 'build_number']
    search_fields = ['package__package_id', 'package__package_name']
    ordering_fields = ['build_number', 'started_at', 'completed_at']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == 'promote':
            return _scope_published_queryset(
                qs.filter(package__isnull=False), self.request.user,
                'promotion', 'builds', 'promote', relation='package__organisations',
            )
        organisation_ids = self.request.user.permission_organisation_ids('builds', 'read')
        if organisation_ids is None:
            return qs
        if not organisation_ids:
            return qs.none()
        return qs.filter(
            Q(package__organisations__in=organisation_ids)
            | Q(bst_source__organisations__in=organisation_ids)
        ).distinct()

    @action(detail=True, methods=['post'])
    def promote(self, request, pk=None):
        """Queue a published build's promotion using target_repo_id; returns promotion_id."""
        if not _has_scoped_permission(request.user, 'published_builds', 'promotion', 'builds', 'promote'):
            raise PermissionDenied()
        data = PromotionRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        scoped_build = self.get_object()
        with transaction.atomic():
            build = Build.objects.select_for_update().get(pk=scoped_build.pk)
            if build.status != 'published':
                raise ValidationError({'detail': 'Build must be published before promoting.'})
            target = next((repo for repo in get_available_promotion_targets(build)
                           if repo.pk == data.validated_data['target_repo_id']), None)
            if target is None:
                raise ValidationError({'target_repo_id': 'Prerequisites not met or already promoted.'})
            promotion, created = Promotion.objects.get_or_create(
                build=build, target_repo=target,
                defaults={'package': build.package, 'status': 'pending', 'promoted_by': request.user},
            )
            if not created:
                if promotion.status != 'failed':
                    raise ValidationError({'target_repo_id': 'Promotion already exists.'})
                promotion.status = 'pending'
                promotion.error_message = ''
                promotion.completed_at = None
                promotion.promoted_by = request.user
                promotion.save(update_fields=['status', 'error_message', 'completed_at', 'promoted_by'])
        from apps.flatpak.tasks import promote_build_task
        promote_build_task.delay(promotion.pk)
        return Response(
            {'promotion_id': promotion.pk, 'status': promotion.status},
            status=status.HTTP_202_ACCEPTED,
        )
    
    @action(detail=True, methods=['get'], authentication_classes=[], permission_classes=[AllowAny])
    def logs(self, request, pk=None):
        """Get build logs (public endpoint)."""
        try:
            build = Build.objects.get(pk=pk)
        except Build.DoesNotExist:
            return Response({'error': 'Build not found'}, status=status.HTTP_404_NOT_FOUND)
        logs = build.logs.all().order_by('timestamp')
        
        log_data = [{
            'id': log.id,
            'message': log.message,
            'level': log.level,
            'timestamp': log.timestamp.strftime('%H:%M:%S')
        } for log in logs]
        
        return Response({
            'build_number': build.build_number,
            'package_id': build.package.package_id if build.package_id else None,
            'bst_source_id': build.bst_source_id,
            'status': build.status,
            'logs': log_data,
            'total_logs': len(log_data)
        })


class ExternalRefViewSet(viewsets.ModelViewSet):
    """List External refs and their versions, inspect logs, or queue pull/publish/promote tasks."""
    queryset = ExternalRef.objects.select_related('repository', 'remote').prefetch_related('versions')
    serializer_class = ExternalRefSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['repository', 'remote', 'status', 'ref']
    search_fields = ['ref', 'display_name']

    def get_permissions(self):
        required = {'create': 'create', 'update': 'update', 'partial_update': 'update', 'destroy': 'delete'}
        if self.request.user.is_authenticated and self.action in required:
            if not _has_scoped_permission(self.request.user, 'externals', required[self.action]):
                raise PermissionDenied()
        return super().get_permissions()

    def perform_create(self, serializer):
        external = serializer.save(created_by=self.request.user)
        apply_default_organisation(external, self.request.user)
        from apps.flatpak.tasks import pull_external_ref_task
        pull_external_ref_task.delay(external.pk)

    def perform_destroy(self, instance):
        from apps.flatpak.tasks import remove_external_ref_from_repos
        remove_external_ref_from_repos(instance)
        instance.delete()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == 'pull':
            return scope_queryset_for_user(queryset, self.request.user, 'externals', 'sync')
        if self.action in ('publish', 'promote'):
            permission = 'externals' if self.action == 'publish' else 'promotion'
            return _scope_published_queryset(
                queryset, self.request.user, permission, 'externals', self.action,
            )
        permission = {'update': 'update', 'partial_update': 'update', 'destroy': 'delete'}.get(self.action, 'read')
        return scope_queryset_for_user(queryset, self.request.user, 'externals', permission)

    @action(detail=True, methods=['post'])
    def pull(self, request, pk=None):
        """Queue a pull from the configured remote followed by publication to the source repository."""
        if not _has_scoped_permission(request.user, 'externals', 'sync'):
            raise PermissionDenied()
        scoped_external = self.get_object()
        with transaction.atomic():
            external = ExternalRef.objects.select_for_update().get(pk=scoped_external.pk)
            if external.status in ('pulling', 'publishing'):
                raise ValidationError({'detail': 'Pull or publish already in progress.'})
            if external.remote_id is None:
                raise ValidationError({'detail': 'No remote configured for this External ref.'})
            external.status = 'pending'
            external.log = ''
            external.error_message = ''
            external.save(update_fields=['status', 'log', 'error_message', 'updated_at'])
        from apps.flatpak.tasks import pull_external_ref_task
        pull_external_ref_task.delay(external.pk)
        return Response({'id': external.pk, 'status': external.status}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        """Queue publication of an already-pulled External ref."""
        if not _has_scoped_permission(request.user, 'published_builds', 'externals', 'externals', 'publish'):
            raise PermissionDenied()
        external = self.get_object()
        if external.status != 'pulled':
            raise ValidationError({'detail': 'External ref must be in pulled state.'})
        from apps.flatpak.tasks import publish_external_ref_task
        publish_external_ref_task.delay(external.pk)
        return Response({'id': external.pk, 'status': 'queued'}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def promote(self, request, pk=None):
        """Promote to target_repo_id using external_ref_version_id or the latest published version."""
        if not _has_scoped_permission(request.user, 'published_builds', 'promotion', 'externals', 'promote'):
            raise PermissionDenied()
        data = PromotionRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        scoped_external = self.get_object()
        with transaction.atomic():
            external = ExternalRef.objects.select_for_update().get(pk=scoped_external.pk)
            versions = external.versions.filter(status='published')
            version_id = data.validated_data.get('external_ref_version_id')
            if version_id is not None:
                version = get_object_or_404(versions, pk=version_id)
            else:
                version = versions.order_by('-pulled_at', '-pk').first()
            if version is None:
                raise ValidationError({'detail': 'No published External ref version available.'})
            target = next((repo for repo in get_available_external_ref_promotion_targets(external, version)
                           if repo.pk == data.validated_data['target_repo_id']), None)
            if target is None:
                raise ValidationError({'target_repo_id': 'Prerequisites not met or already promoted.'})
            promotion = ExternalRefPromotion.objects.create(
                external_ref=external, external_ref_version=version, target_repo=target,
                status='pending', promoted_by=request.user,
            )
        from apps.flatpak.tasks import promote_external_ref_task
        promote_external_ref_task.delay(promotion.pk)
        return Response(
            {'promotion_id': promotion.pk, 'external_ref_version_id': version.pk, 'status': promotion.status},
            status=status.HTTP_202_ACCEPTED,
        )


class PromotionViewSet(viewsets.ReadOnlyModelViewSet):
    """Poll Flatpak build promotions by ID, build, target repository, or status."""
    queryset = Promotion.objects.all()
    serializer_class = PromotionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['build', 'target_repo', 'status']

    def get_queryset(self):
        return _scope_published_queryset(
            super().get_queryset(), self.request.user,
            'promotion', 'builds', 'promote', relation='package__organisations',
        )


class ExternalRefPromotionViewSet(viewsets.ReadOnlyModelViewSet):
    """Poll External version promotions by ID, ref, version, target repository, or status."""
    queryset = ExternalRefPromotion.objects.all()
    serializer_class = ExternalRefPromotionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['external_ref', 'external_ref_version', 'target_repo', 'status']

    def get_queryset(self):
        return _scope_published_queryset(
            super().get_queryset(), self.request.user,
            'promotion', 'externals', 'promote', relation='external_ref__organisations',
        )


class BuildArtifactViewSet(viewsets.ModelViewSet):
    """
    API endpoint for build artifacts.
    """
    queryset = BuildArtifact.objects.all()
    serializer_class = BuildArtifactSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated()]
        return [CanBuild()]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['build']
    search_fields = ['filename']


class TokenViewSet(viewsets.ModelViewSet):
    """
    API endpoint for repository tokens.
    """
    queryset = Token.objects.all()
    serializer_class = TokenSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated()]
        return [CanRepoAdmin()]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['repository', 'token_type', 'is_active']
    ordering_fields = ['created_at', 'expires_at']
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

@api_view(['GET'])
@permission_classes([AllowAny])
def git_branches(request):
    """
    Lookup available branches from a Git repository.
    """
    import os
    import subprocess
    from django.http import JsonResponse
    
    repo_url = request.GET.get('repo_url')
    if not repo_url:
        return JsonResponse({'error': 'repo_url parameter required'}, status=400)
    
    try:
        # Argument list, never a shell string: repo_url is untrusted input.
        result = subprocess.run(
            ['git', 'ls-remote', '--heads', '--', repo_url],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': '/bin/true'},
        )
        
        if result.returncode != 0:
            return JsonResponse({'branches': ['master', 'main']})  # fallback
        
        # Parse branch names from output
        branches = []
        for line in result.stdout.strip().split('\n'):
            if line and '\t' in line:
                parts = line.split('\t')
                if len(parts) >= 2 and parts[1].startswith('refs/heads/'):
                    branch = parts[1].replace('refs/heads/', '')
                    branches.append(branch)
        
        # Ensure we always have some branches
        if not branches:
            branches = ['master', 'main']
        elif 'master' not in branches and 'main' not in branches:
            branches.append('master')
        
        return JsonResponse({'branches': sorted(set(branches))})
        
    except Exception:
        # Fallback on any error
        return JsonResponse({'branches': ['master', 'main']})


@api_view(['GET'])
@permission_classes([AllowAny])
def version(request):
    """Report the running application version."""
    from version import VERSION, BUILD_DATE
    return Response({'version': VERSION, 'build_date': BUILD_DATE})

