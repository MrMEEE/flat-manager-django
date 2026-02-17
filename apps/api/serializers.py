from rest_framework import serializers
from apps.users.models import User, UserProfile, APIToken
from apps.flatpak.models import GPGKey, Repository, RepositorySubset, Build, BuildArtifact, BuildLog, Token


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 
                  'is_repo_admin', 'is_build_admin', 'created_at']
        read_only_fields = ['id', 'created_at']


class UserProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    
    class Meta:
        model = UserProfile
        fields = ['user', 'bio', 'phone', 'organization']


class APITokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = APIToken
        fields = ['id', 'name', 'token', 'created_at', 'last_used', 'expires_at', 'is_active']
        read_only_fields = ['id', 'token', 'created_at', 'last_used']


class GPGKeySerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    
    class Meta:
        model = GPGKey
        fields = ['id', 'name', 'email', 'key_id', 'fingerprint', 'public_key', 
                  'passphrase_hint', 'created_by', 'created_at', 'updated_at', 'is_active']
        read_only_fields = ['id', 'created_at', 'updated_at']
        # Never expose private_key through API


class GPGKeyListSerializer(serializers.ModelSerializer):
    """Lighter serializer for list views"""
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = GPGKey
        fields = ['id', 'name', 'email', 'key_id', 'fingerprint', 'is_active', 
                  'created_by_username', 'created_at']


class RepositorySubsetSerializer(serializers.ModelSerializer):
    class Meta:
        model = RepositorySubset
        fields = ['id', 'name', 'collection_id', 'base_url']
        read_only_fields = ['id']


class RepositorySerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    gpg_key = GPGKeyListSerializer(read_only=True)
    gpg_key_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    build_count = serializers.SerializerMethodField()
    subsets = RepositorySubsetSerializer(many=True, read_only=True)
    repo_path = serializers.CharField(read_only=True)
    public_key_path = serializers.SerializerMethodField()
    parent_repos = serializers.PrimaryKeyRelatedField(many=True, queryset=Repository.objects.all(), required=False)
    parent_repo_names = serializers.SerializerMethodField()
    child_repo_names = serializers.SerializerMethodField()
    
    class Meta:
        model = Repository
        fields = ['id', 'name', 'collection_id', 'description', 'gpg_key', 'gpg_key_id',
                  'parent_repos', 'parent_repo_names', 'child_repo_names',
                  'subsets', 'repo_path', 'public_key_path', 'created_by', 'created_at', 
                  'updated_at', 'is_active', 'build_count']
        read_only_fields = ['id', 'created_at', 'updated_at', 'repo_path']
    
    def get_build_count(self, obj):
        return obj.builds.count()
    
    def get_public_key_path(self, obj):
        return obj.get_public_key_path()
    
    def get_parent_repo_names(self, obj):
        return [parent.name for parent in obj.parent_repos.all()]
    
    def get_child_repo_names(self, obj):
        return [child.name for child in obj.child_repos.all()]


class BuildArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildArtifact
        fields = ['id', 'filename', 'file_path', 'file_size', 'checksum', 'uploaded_at']
        read_only_fields = ['id', 'uploaded_at']


class BuildLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildLog
        fields = ['id', 'message', 'level', 'timestamp']
        read_only_fields = ['id', 'timestamp']


class BuildSerializer(serializers.ModelSerializer):
    repository = RepositorySerializer(read_only=True)
    repository_id = serializers.IntegerField(write_only=True)
    created_by = UserSerializer(read_only=True)
    artifacts = BuildArtifactSerializer(many=True, read_only=True)
    logs = BuildLogSerializer(many=True, read_only=True)
    
    class Meta:
        model = Build
        fields = ['id', 'build_id', 'app_id', 'version', 'git_repo_url', 'git_branch', 'source_commit',
                  'branch', 'arch', 'status', 'commit_hash', 'repository', 'repository_id', 
                  'created_by', 'created_at', 'started_at', 'completed_at', 'published_at',
                  'error_message', 'build_number', 'artifacts', 'logs']
        read_only_fields = ['id', 'build_id', 'source_commit', 'commit_hash', 
                            'created_at', 'started_at', 'completed_at', 'published_at', 'build_number']


class TokenSerializer(serializers.ModelSerializer):
    repository = RepositorySerializer(read_only=True)
    repository_id = serializers.IntegerField(write_only=True)
    
    class Meta:
        model = Token
        fields = ['id', 'name', 'token', 'token_type', 'repository', 'repository_id',
                  'created_at', 'expires_at', 'is_active']
        read_only_fields = ['id', 'token', 'created_at']
