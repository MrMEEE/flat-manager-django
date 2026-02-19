from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

from apps.users.models import User, UserProfile, APIToken
from apps.flatpak.models import GPGKey, Repository, RepositorySubset, Package, Build, BuildArtifact, BuildLog, Token
from apps.flatpak.utils.gpg import generate_gpg_key, import_gpg_key
from .serializers import (
    UserSerializer, UserProfileSerializer, APITokenSerializer,
    GPGKeySerializer, GPGKeyListSerializer, RepositorySerializer, RepositorySubsetSerializer,
    PackageSerializer, BuildSerializer, BuildArtifactSerializer, BuildLogSerializer, TokenSerializer
)


class UserViewSet(viewsets.ModelViewSet):
    """
    API endpoint for users.
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
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


class GPGKeyViewSet(viewsets.ModelViewSet):
    """
    API endpoint for GPG keys.
    """
    queryset = GPGKey.objects.all()
    serializer_class = GPGKeySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'email', 'key_id', 'fingerprint']
    ordering_fields = ['name', 'created_at']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return GPGKeyListSerializer
        return GPGKeySerializer
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
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
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active', 'collection_id']
    search_fields = ['name', 'description', 'collection_id']
    ordering_fields = ['name', 'created_at']
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['get'])
    def builds(self, request, pk=None):
        """Get all builds for a repository."""
        repository = self.get_object()
        builds = repository.builds.all()
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
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['repository']
    search_fields = ['name', 'collection_id']


class PackageViewSet(viewsets.ModelViewSet):
    """
    API endpoint for packages (main configuration records).
    """
    queryset = Package.objects.all()
    serializer_class = PackageSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'repository', 'arch', 'branch']
    search_fields = ['package_id', 'package_name']
    ordering_fields = ['created_at', 'build_number']
    
    def get_permissions(self):
        """Allow unauthenticated access to logs and retrieve endpoints."""
        if self.action in ['logs', 'retrieve']:
            return [AllowAny()]
        return super().get_permissions()
    
    def perform_create(self, serializer):
        package = serializer.save(created_by=self.request.user)
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
        if package.status in ['completed', 'failed', 'cancelled']:
            return Response(
                {'error': 'Build cannot be cancelled in current state'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        package.status = 'cancelled'
        package.save()
        return Response({'status': 'Build cancelled'})
    
    @action(detail=True, methods=['get'], authentication_classes=[], permission_classes=[AllowAny])
    def logs(self, request, pk=None):
        """Get build logs with live updates (public endpoint)."""
        # Manually get build to bypass permission check on get_object()
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
            'build_id': package.package_id,
            'status': package.status,
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
        commit_package_task.delay(build.id)
        
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
        publish_package_task.delay(build.id)
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
    
    @action(detail=True, methods=['get'])
    def logs(self, request, pk=None):
        """Get build logs."""
        package = self.get_object()
        logs = build.logs.all()
        serializer = BuildLogSerializer(logs, many=True)
        return Response(serializer.data)


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
    
    @action(detail=True, methods=['get'], authentication_classes=[], permission_classes=[AllowAny])
    def logs(self, request, pk=None):
        """Get build logs (public endpoint)."""
        build = self.get_object()
        logs = build.logs.all().order_by('timestamp')
        
        log_data = [{
            'id': log.id,
            'message': log.message,
            'level': log.level,
            'timestamp': log.timestamp.strftime('%H:%M:%S')
        } for log in logs]
        
        return Response({
            'build_number': build.build_number,
            'package_id': build.package.package_id,
            'status': build.status,
            'logs': log_data,
            'total_logs': len(log_data)
        })


class BuildArtifactViewSet(viewsets.ModelViewSet):
    """
    API endpoint for build artifacts.
    """
    queryset = BuildArtifact.objects.all()
    serializer_class = BuildArtifactSerializer
    permission_classes = [IsAuthenticated]
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
    import subprocess
    from django.http import JsonResponse
    
    repo_url = request.GET.get('repo_url')
    if not repo_url:
        return JsonResponse({'error': 'repo_url parameter required'}, status=400)
    
    try:
        # Simple approach - just run the command and parse output
        # Using shell=True to avoid subprocess hanging issues
        cmd = f"timeout 3 git ls-remote --heads '{repo_url}' 2>/dev/null"
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 124:  # timeout
            return JsonResponse({'branches': ['master', 'main']})  # fallback
        elif result.returncode != 0:
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

