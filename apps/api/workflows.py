from django.shortcuts import get_object_or_404
from django.views import View
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.flatpak import models as flatpak_models, views as flatpak_views
from apps.rpm import models as rpm_models, views as rpm_views
from apps.users import models as user_models, views as user_views
from apps.users.mixins import scope_queryset_for_user
from .adapters import invoke_workflow
from .permissions import IsAdmin


def safe_serializer(model, fields):
    meta = type('Meta', (), {'model': model, 'fields': fields, 'read_only_fields': fields})
    return type(f'{model.__name__}WorkflowSerializer', (serializers.ModelSerializer,), {'Meta': meta})


def require_access(request, resource, permission, admin=False):
    if admin:
        if not IsAdmin().has_permission(request, None):
            raise PermissionDenied()
    elif not flatpak_views._has_scoped_permission(request.user, resource, permission):
        raise PermissionDenied()


class WorkflowResourceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    resource = None
    admin_only = False
    scope_relation = 'organisations'
    handlers = {}
    handler_kwargs = {}

    def get_queryset(self):
        permission = {'create': 'create', 'update': 'update', 'partial_update': 'update',
                      'destroy': 'delete'}.get(self.action, 'read')
        require_access(self.request, self.resource, permission, self.admin_only)
        queryset = super().get_queryset()
        if not self.admin_only:
            queryset = scope_queryset_for_user(
                queryset, self.request.user, self.resource, permission, self.scope_relation)
        return queryset.order_by('pk')

    def mutate(self, operation, pk=None):
        handler = self.handlers.get(operation)
        if handler is None:
            raise MethodNotAllowed(self.request.method)
        permission = {'create': 'create', 'update': 'update', 'destroy': 'delete'}[operation]
        require_access(self.request, self.resource, permission, self.admin_only)
        instance = self.get_object() if pk is not None else None
        if not self.admin_only:
            restrict_related_scope(self.request, self.resource, permission)
        kwargs = {'pk': instance.pk} if instance is not None else {}
        for name, model in self.handler_kwargs.get(operation, {}).items():
            kwargs[name] = related_id(self.request, name, model)
        return invoke_workflow(self.request, handler, kwargs=kwargs, instance=instance,
                               partial=self.request.method == 'PATCH')

    def create(self, request):
        return self.mutate('create')

    def update(self, request, pk=None):
        return self.mutate('update', pk)

    def partial_update(self, request, pk=None):
        return self.mutate('update', pk)

    def destroy(self, request, pk=None):
        return self.mutate('destroy', pk)


def related_id(request, name, model):
    field = serializers.IntegerField(min_value=1)
    value = field.run_validation(request.data.get(name))
    return get_object_or_404(model, pk=value).pk


def restrict_related_scope(request, resource, permission='update'):
    if not isinstance(request.data, dict):
        raise ValidationError({'detail': 'Expected a JSON object.'})
    for name in ('organisations', 'repository'):
        if name not in request.data:
            continue
        if name == 'organisations':
            values = serializers.ListField(child=serializers.IntegerField(min_value=1)).run_validation(request.data[name])
            allowed = request.user.permission_organisation_ids(resource, permission)
            if allowed is not None and not set(values).issubset(set(allowed)):
                raise PermissionDenied('Cannot assign organisations outside your scope.')
        else:
            value = serializers.IntegerField(min_value=1).run_validation(request.data[name])
            get_object_or_404(scope_queryset_for_user(
                flatpak_models.Repository.objects.all(), request.user, 'repositories'), pk=value)


def resource_viewset(name, model, fields, resource, *, handlers=None, admin=False,
                     relation='organisations', handler_kwargs=None):
    return type(name, (WorkflowResourceViewSet,), {
        '__module__': __name__, 'queryset': model.objects.all(),
        'serializer_class': safe_serializer(model, fields), 'resource': resource,
        'admin_only': admin, 'scope_relation': relation,
        'handlers': handlers or {}, 'handler_kwargs': handler_kwargs or {},
    })


def workflow_action(viewset, name, handler, resource, permission, *, detail=True,
                    admin=False, model=None, relation='organisations', lookup='pk',
                    extra_ids=None, method='post', published=None):
    def execute(self, request, pk=None):
        if published and not admin:
            if not flatpak_views._has_scoped_permission(
                request.user, 'published_builds', published, resource, permission,
            ):
                raise PermissionDenied()
        else:
            require_access(request, resource, permission, admin)
        if not isinstance(request.data, dict):
            raise ValidationError({'detail': 'Expected a JSON object.'})
        if name == 'bulk_import':
            class ImportInput(serializers.Serializer):
                repository_id = serializers.IntegerField(min_value=1)
                remote_id = serializers.IntegerField(min_value=1)
                refs = serializers.ListField(
                    child=serializers.RegexField(r'^(app|runtime)/[^/]+/[^/]+/[^/]+$'),
                    allow_empty=False, max_length=1000,
                )
            incoming = ImportInput(data=request.data)
            incoming.is_valid(raise_exception=True)
        if name == 'signing_key' and request.data.get('signing_key_id'):
            key_id = serializers.IntegerField(min_value=1).run_validation(request.data['signing_key_id'])
            get_object_or_404(scope_queryset_for_user(
                flatpak_models.GPGKey.objects.all(), request.user, 'gpg_keys'), pk=key_id)
        kwargs = {}
        if detail:
            queryset = model.objects.all() if model else self.queryset.all()
            if not admin:
                if published:
                    queryset = flatpak_views._scope_published_queryset(
                        queryset, request.user, published, resource, permission, relation)
                else:
                    queryset = scope_queryset_for_user(queryset, request.user, resource, permission, relation)
            instance = get_object_or_404(queryset, pk=pk)
            kwargs[lookup] = instance.pk
        for key, related_model in (extra_ids or {}).items():
            kwargs[key] = related_id(request, key, related_model)
        return invoke_workflow(request, handler, kwargs=kwargs, method=method)
    execute.__name__ = name
    execute.__doc__ = handler.__doc__ or f'Run {name.replace("_", " ")} using the web workflow JSON/form fields.'
    execute.workflow = {'resource': resource, 'permission': permission, 'admin_only': admin,
                        'extra_ids': list((extra_ids or {}).keys())}
    setattr(viewset, name, action(detail=detail, methods=['post'], url_path=name.replace('_', '-'))(execute))


FlatpakRemoteViewSet = resource_viewset(
    'FlatpakRemoteViewSet', flatpak_models.FlatpakRemote,
    ['id', 'name', 'url', 'is_active', 'priority'], 'config', admin=True,
    handlers={'create': flatpak_views.FlatpakRemoteCreateView, 'destroy': flatpak_views.FlatpakRemoteDeleteView})
OrganisationViewSet = resource_viewset(
    'OrganisationViewSet', flatpak_models.Organisation,
    ['id', 'name', 'description', 'responsible_name', 'responsible_email'], 'users', admin=True,
    handlers={'create': flatpak_views.OrganisationCreateView, 'update': flatpak_views.OrganisationUpdateView,
              'destroy': flatpak_views.OrganisationDeleteView})
BuildStreamSourceViewSet = resource_viewset(
    'BuildStreamSourceViewSet', flatpak_models.BuildStreamSource,
    ['id', 'name', 'repository', 'git_repo_url', 'git_branch', 'bst_element', 'bst_version',
     'status', 'error_message', 'build_number', 'organisations'], 'buildstreams',
    handlers={'create': flatpak_views.BuildStreamSourceCreateView, 'update': flatpak_views.BuildStreamSourceUpdateView,
              'destroy': flatpak_views.BuildStreamSourceDeleteView})
BstPromotionViewSet = resource_viewset(
    'BstPromotionViewSet', flatpak_models.BstPromotion,
    ['id', 'build', 'bst_source', 'target_repo', 'status', 'error_message', 'promoted_by',
     'created_at', 'completed_at'], 'buildstreams', relation='bst_source__organisations')
ClientViewSet = resource_viewset(
    'ClientViewSet', flatpak_models.Client,
    ['id', 'hostname', 'serial_number', 'machine_type', 'last_checkin', 'organisations',
     'lockdown', 'installed_flatpaks', 'foreign_flatpaks', 'outdated_flatpaks',
     'user_flatpaks', 'remotes', 'managed_remotes', 'updated_at'], 'clients',
    handlers={'destroy': flatpak_views.ClientDeleteView})
PermissionGroupViewSet = resource_viewset(
    'PermissionGroupViewSet', user_models.PermissionGroup,
    ['id', 'name', 'description', 'organisation', 'is_predefined', 'users'], 'users', admin=True,
    handlers={'create': user_views.PermissionGroupCreateView, 'update': user_views.PermissionGroupUpdateView,
              'destroy': user_views.PermissionGroupDeleteView})
LDAPSourceViewSet = resource_viewset(
    'LDAPSourceViewSet', user_models.LDAPSource,
    ['id', 'name', 'hostname', 'port', 'protocol', 'verify_certs', 'server_type',
     'bind_dn', 'base_dn', 'group_base_dn', 'group_membership', 'ldap_filter',
     'attr_username', 'attr_first_name', 'attr_last_name', 'attr_email', 'is_active'], 'users', admin=True,
    handlers={'create': user_views.LDAPSourceCreateView, 'update': user_views.LDAPSourceUpdateView,
              'destroy': user_views.LDAPSourceDeleteView})
LDAPGroupMappingViewSet = resource_viewset(
    'LDAPGroupMappingViewSet', user_models.LDAPGroupMapping,
    ['id', 'source', 'ldap_group_dn', 'organisation', 'role'], 'users', admin=True,
    handlers={'create': user_views.LDAPGroupMappingCreateView, 'destroy': user_views.LDAPGroupMappingDeleteView},
    handler_kwargs={'create': {'source_pk': user_models.LDAPSource}, 'destroy': {'source_pk': user_models.LDAPSource}})
RpmPackageViewSet = resource_viewset(
    'RpmPackageViewSet', rpm_models.RpmPackage,
    ['id', 'name', 'git_repo_url', 'git_branch', 'spec_file', 'distributions', 'default_repos',
        'organisations', 'status', 'available_version', 'upstream_version', 'description',
        'allow_internet_access', 'cleanup_on_success', 'upstream_url'], 'rpms',
    handlers={'create': rpm_views.RpmPackageCreateView, 'update': rpm_views.RpmPackageUpdateView,
              'destroy': rpm_views.RpmPackageDeleteView})
RpmBuildViewSet = resource_viewset(
    'RpmBuildViewSet', rpm_models.RpmBuild,
    ['id', 'package', 'distribution', 'build_number', 'status', 'error_message', 'version',
     'selected_repos', 'rpm_files', 'started_at', 'completed_at'], 'rpms', relation='package__organisations',
    handlers={'destroy': rpm_views.RpmBuildDeleteView})
RpmDistributionViewSet = resource_viewset(
    'RpmDistributionViewSet', rpm_models.RpmDistribution,
    ['id', 'name', 'display_name', 'arch', 'rhel_version', 'is_active'], 'repositories', admin=True)
RpmRepositoryViewSet = resource_viewset(
    'RpmRepositoryViewSet', rpm_models.RpmRepository,
    ['id', 'distribution', 'name', 'enabled', 'source'], 'repositories', admin=True)
SatelliteServerViewSet = resource_viewset(
    'SatelliteServerViewSet', rpm_models.SatelliteServer,
    ['id', 'name', 'url', 'login', 'ssl_verify'], 'config', admin=True,
    handlers={'create': rpm_views.RpmSatelliteServerCreateView, 'destroy': rpm_views.RpmSatelliteServerDeleteView})
SatelliteRepositoryViewSet = resource_viewset(
    'SatelliteRepositoryViewSet', rpm_models.SatelliteRepository,
    ['id', 'server', 'name', 'organization', 'product', 'repository_id'], 'config', admin=True,
    handlers={'create': rpm_views.RpmSatelliteRepositoryAddView, 'destroy': rpm_views.RpmSatelliteRepositoryDeleteView},
    handler_kwargs={'create': {'server_pk': rpm_models.SatelliteServer}})
RpmDestinationViewSet = resource_viewset(
    'RpmDestinationViewSet', rpm_models.RpmPackageDistributionDestination,
    ['id', 'package', 'distribution', 'repository'], 'repositories', admin=True,
    handlers={'destroy': rpm_views.RpmPackageRemoveDestinationView})
RpmSigningKeyViewSet = resource_viewset(
    'RpmSigningKeyViewSet', rpm_models.RpmPackageSigningKey,
    ['id', 'package', 'distribution', 'signing_key'], 'repositories', admin=True)


RESOURCE_ROUTES = {
    'flatpak-remotes': FlatpakRemoteViewSet, 'organisations': OrganisationViewSet,
    'bst-sources': BuildStreamSourceViewSet, 'bst-promotions': BstPromotionViewSet,
    'clients': ClientViewSet, 'permission-groups': PermissionGroupViewSet,
    'ldap-sources': LDAPSourceViewSet, 'ldap-group-mappings': LDAPGroupMappingViewSet,
    'rpm-packages': RpmPackageViewSet, 'rpm-builds': RpmBuildViewSet,
    'rpm-distributions': RpmDistributionViewSet, 'rpm-repositories': RpmRepositoryViewSet,
    'satellite-servers': SatelliteServerViewSet, 'satellite-repositories': SatelliteRepositoryViewSet,
    'rpm-destinations': RpmDestinationViewSet, 'rpm-signing-keys': RpmSigningKeyViewSet,
}


class AccessViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsAdmin]


def access_viewset(model, fields):
    serializer = safe_serializer(model, fields)
    serializer.Meta.read_only_fields = ['id']
    return type(f'{model.__name__}AccessViewSet', (AccessViewSet,), {
        '__module__': __name__, 'queryset': model.objects.order_by('pk'), 'serializer_class': serializer,
    })


RESOURCE_ROUTES.update({
    'flatpak-remotes': access_viewset(flatpak_models.FlatpakRemote, ['id', 'name', 'url', 'is_active', 'priority']),
    'rpm-distributions': access_viewset(rpm_models.RpmDistribution,
                                     ['id', 'name', 'display_name', 'mock_config', 'arch', 'rhel_version', 'is_active']),
    'ldap-group-mappings': access_viewset(user_models.LDAPGroupMapping,
                                       ['id', 'source', 'ldap_group_dn', 'role', 'organisation']),
    'user-roles': access_viewset(user_models.UserRole, ['id', 'user', 'role', 'organisation']),
    'permission-grants': access_viewset(user_models.PermissionGrant,
                                     ['id', 'user', 'resource', 'action', 'granted', 'organisation']),
    'group-permissions': access_viewset(user_models.PermissionGroupPermission,
                                     ['id', 'group', 'resource', 'action', 'granted', 'organisation']),
})
FlatpakRemoteViewSet = RESOURCE_ROUTES['flatpak-remotes']
RpmDistributionViewSet = RESOURCE_ROUTES['rpm-distributions']


class SiteConfigViewSet(WorkflowResourceViewSet):
    queryset = flatpak_models.SiteConfig.objects.all()
    resource = 'config'
    admin_only = True
    handlers = {'update': flatpak_views.ConfigView}
    serializer_class = safe_serializer(flatpak_models.SiteConfig, [
        field.name for field in flatpak_models.SiteConfig._meta.fields
    ])

    def list(self, request):
        require_access(request, 'config', 'read', admin=True)
        return Response(self.get_serializer(flatpak_models.SiteConfig.get_solo()).data)

    def mutate(self, operation, pk=None):
        if operation != 'update':
            raise MethodNotAllowed(self.request.method)
        require_access(self.request, 'config', 'update', admin=True)
        instance = get_object_or_404(self.queryset, pk=pk)
        return invoke_workflow(self.request, flatpak_views.ConfigView, instance=instance,
                               partial=self.request.method == 'PATCH')


RESOURCE_ROUTES['site-config'] = SiteConfigViewSet


@action(detail=True, methods=['post'])
def build(self, request, pk=None):
    """Immediately queue a BuildStream source build; no request body fields are required."""
    require_access(request, 'buildstreams', 'build')
    source = get_object_or_404(scope_queryset_for_user(
        flatpak_models.BuildStreamSource.objects.all(), request.user, 'buildstreams', 'build'), pk=pk)
    if source.status in ('building', 'committing', 'publishing'):
        raise ValidationError({'detail': 'Source is already building or publishing.'})
    if source.status != 'pending':
        source.build_number += 1
    source.status = 'pending'
    source.error_message = ''
    source.save(update_fields=['status', 'error_message', 'build_number'])
    from apps.flatpak.tasks import buildstream_build_task
    buildstream_build_task.delay(source.pk)
    return Response({'id': source.pk, 'status': 'queued'}, status=202)


BuildStreamSourceViewSet.build = build


class WorkflowCatalogViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        from .urls import router
        resources = []
        for prefix, viewset, basename in router.registry:
            if viewset is WorkflowCatalogViewSet:
                continue
            resources.append({
                'path': f'/api/{prefix}/',
                'actions': [{
                    'path': f'/api/{prefix}/' + ('{id}/' if operation.detail else '') + operation.url_path + '/',
                    'methods': [method.upper() for method in operation.mapping],
                    'description': operation.__doc__ or '',
                    **getattr(operation, 'workflow', {}),
                } for operation in viewset.get_extra_actions()],
            })
        return Response({
            'authentication': 'Authorization: Token <DRF token>',
            'workflow_body': 'JSON object using the existing web form field names or JSON fields.',
            'nested_ids': 'Nested route identifiers are supplied as JSON fields listed in extra_ids.',
            'resources': resources,
        })


RESOURCE_ROUTES['workflows'] = WorkflowCatalogViewSet


class GPGKeyRenewWorkflow(View):
    """Renew a signing key using key_lifetime (0, 1y, 2y, 5y, or 10y)."""

    def post(self, request, pk):
        return flatpak_views.gpgkey_renew(request, pk)


def register_workflows(router, api_views):
    for prefix, viewset in RESOURCE_ROUTES.items():
        router.register(prefix, viewset, basename=prefix)

    workflow_action(api_views.GPGKeyViewSet, 'renew', GPGKeyRenewWorkflow,
                    'gpg_keys', 'update')
    workflow_action(LDAPSourceViewSet, 'test_connection', user_views.LDAPSourceTestConnectionView,
                    'users', 'admin', detail=False, admin=True)
    workflow_action(LDAPSourceViewSet, 'test_search', user_views.LDAPSourceTestSearchView,
                    'users', 'admin', detail=False, admin=True)
    workflow_action(RpmPackageViewSet, 'scan_spec_files', rpm_views.RpmScanSpecFilesView,
                    'rpms', 'build', detail=False, method='get')
    workflow_action(RpmPackageViewSet, 'scan_branches', rpm_views.RpmScanBranchesView,
                    'rpms', 'build', detail=False, method='get')

    workflow_action(FlatpakRemoteViewSet, 'toggle', flatpak_views.FlatpakRemoteToggleView,
                    'config', 'update', admin=True)
    workflow_action(api_views.ExternalRefViewSet, 'bulk_import', flatpak_views.ExternalRefBulkImportView,
                    'externals', 'create', detail=False, admin=True)
    workflow_action(api_views.ExternalRefViewSet, 'unpublish', flatpak_views.ExternalRefUnpublishView,
                    'externals', 'publish', published='externals')

    for name, handler, permission in (
        ('retry', flatpak_views.PackageRetryView, 'build'),
        ('republish', flatpak_views.PackageRepublishView, 'publish'),
        ('check_upstream', flatpak_views.PackageCheckUpstreamView, 'read'),
        ('check_available', flatpak_views.PackageCheckAvailableView, 'read'),
    ):
        workflow_action(api_views.PackageViewSet, name, handler, 'flatpaks', permission,
                published='publish' if name == 'republish' else None)
    workflow_action(api_views.RepositoryViewSet, 'update_metadata', flatpak_views.RepositoryUpdateMetadataView,
                    'repositories', 'update')

    for name, handler, resource, permission, dedicated in (
        ('unpublish', flatpak_views.BuildUnpublishView, 'flatpaks', 'publish', 'publish'),
        ('recommit', flatpak_views.BuildRecommitView, 'builds', 'build', 'commit'),
        ('delete', flatpak_views.BuildDeleteView, 'builds', 'delete', None),
        ('cancel', flatpak_views.BuildCancelView, 'builds', 'build', None),
    ):
        workflow_action(api_views.BuildViewSet, name, handler, resource, permission,
                        relation='package__organisations', published=dedicated)
    workflow_action(api_views.BuildViewSet, 'bst_promote', flatpak_views.BstPromoteView,
                    'buildstreams', 'promote', relation='bst_source__organisations',
                    lookup='build_pk', published='promotion')
    for viewset, resource, relation, retry, delete in (
        (api_views.PromotionViewSet, 'builds', 'package__organisations',
         flatpak_views.PromotionRetryView, flatpak_views.PromotionDeleteView),
        (api_views.ExternalRefPromotionViewSet, 'externals', 'external_ref__organisations',
         flatpak_views.ExternalRefPromotionRetryView, flatpak_views.ExternalRefPromotionDeleteView),
        (BstPromotionViewSet, 'buildstreams', 'bst_source__organisations',
         flatpak_views.BstPromotionRetryView, flatpak_views.BstPromotionDeleteView),
    ):
        workflow_action(viewset, 'retry', retry, resource, 'promote', relation=relation, published='promotion')
        workflow_action(viewset, 'delete', delete, resource, 'promote', relation=relation, published='promotion')

    workflow_action(BuildStreamSourceViewSet, 'retry', flatpak_views.BuildStreamSourceRetryView, 'buildstreams', 'build')
    workflow_action(BuildStreamSourceViewSet, 'force_rebuild', flatpak_views.BuildStreamSourceForceRebuildView,
                    'buildstreams', 'build')
    workflow_action(BuildStreamSourceViewSet, 'integrity_check', flatpak_views.BuildStreamIntegrityCheckView,
                    'buildstreams', 'read', detail=False, admin=True, method='get')
    workflow_action(ClientViewSet, 'assign_organisations', flatpak_views.ClientAssignOrgsView,
                    'clients', 'update', admin=True)
    workflow_action(ClientViewSet, 'lockdown', flatpak_views.ClientLockdownToggleView, 'clients', 'update')
    workflow_action(PermissionGroupViewSet, 'memberships', user_views.PermissionGroupDetailView,
                    'users', 'admin', admin=True)

    for name, handler in (
        ('cleanup', flatpak_views.RunCleanupNowView),
        ('sync_repositories', flatpak_views.SyncReposView),
        ('check_external_updates', flatpak_views.RunCheckExternalRefUpdatesView),
        ('scan_available_versions', flatpak_views.RunAvailableVersionScanView),
        ('scan_upstream_versions', flatpak_views.RunUpstreamVersionScanView),
        ('repair_permissions', flatpak_views.ScanRepairRepoTmpPermissionsView),
        ('scan_orphaned_refs', flatpak_views.ScanOrphanedRefsView),
        ('prune_orphaned_ref', flatpak_views.PruneOrphanedRefsView),
        ('prune_orphaned_refs', flatpak_views.BulkPruneOrphanedRefsView),
    ):
        workflow_action(SiteConfigViewSet, name, handler, 'config', 'update', detail=False, admin=True)

    for name, handler in (
        ('build', rpm_views.RpmPackageBuildView),
        ('build_with_number', rpm_views.RpmPackageBuildWithNumberView),
        ('check_upstream', rpm_views.RpmPackageCheckUpstreamView),
        ('check_available', rpm_views.RpmPackageCheckAvailableView),
    ):
        workflow_action(RpmPackageViewSet, name, handler, 'rpms', 'build')
    workflow_action(RpmPackageViewSet, 'toggle_repository', rpm_views.RpmPackageRepositoryToggleView,
                    'rpms', 'update', extra_ids={'repo_pk': rpm_models.RpmRepository})
    workflow_action(RpmPackageViewSet, 'signing_key', rpm_views.RpmPackageSigningKeyView,
                    'rpms', 'update', lookup='pkg_pk', extra_ids={'dist_pk': rpm_models.RpmDistribution})
    workflow_action(RpmPackageViewSet, 'assign_destination', rpm_views.RpmPackageAssignDestinationView,
                    'rpms', 'update')
    for name, handler in (
        ('retry', rpm_views.RpmBuildRetryView), ('repush', rpm_views.RpmBuildRepushView),
        ('cancel', rpm_views.RpmBuildCancelView),
    ):
        workflow_action(RpmBuildViewSet, name, handler, 'rpms', 'build', relation='package__organisations')
    workflow_action(RpmBuildViewSet, 'logs', rpm_views.RpmBuildLogsApiView, 'rpms', 'read',
                    relation='package__organisations', method='get')
    workflow_action(RpmDistributionViewSet, 'sync', rpm_views.RpmDistributionSyncView,
                    'repositories', 'update', admin=True, detail=False)
    workflow_action(RpmDistributionViewSet, 'toggle', rpm_views.RpmDistributionToggleView,
                    'repositories', 'update', admin=True)
    workflow_action(RpmDistributionViewSet, 'sync_repos', rpm_views.RpmDistributionSyncReposView,
                    'repositories', 'update', admin=True)
    workflow_action(RpmRepositoryViewSet, 'toggle', rpm_views.RpmRepositoryToggleView,
                    'repositories', 'update', admin=True, lookup='repo_pk')
    for name, handler in (
        ('organisations', rpm_views.RpmSatelliteFetchOrgsView),
        ('products', rpm_views.RpmSatelliteFetchProductsView),
        ('repositories', rpm_views.RpmSatelliteFetchReposView),
    ):
        workflow_action(SatelliteServerViewSet, f'fetch_{name}', handler, 'config', 'admin',
                        detail=False, admin=True, method='get')