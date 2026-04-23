from django.urls import path
from . import views

app_name = 'rpm'

urlpatterns = [
    # Packages
    path('rpms/', views.RpmPackageListView.as_view(), name='package_list'),
    path('rpms/create/', views.RpmPackageCreateView.as_view(), name='package_create'),
    path('rpms/<int:pk>/', views.RpmPackageDetailView.as_view(), name='package_detail'),
    path('rpms/<int:pk>/edit/', views.RpmPackageUpdateView.as_view(), name='package_edit'),
    path('rpms/<int:pk>/delete/', views.RpmPackageDeleteView.as_view(), name='package_delete'),
    path('rpms/<int:pk>/build/', views.RpmPackageBuildView.as_view(), name='package_build'),
    path('rpms/<int:pk>/check-upstream/', views.RpmPackageCheckUpstreamView.as_view(), name='package_check_upstream'),
    path('rpms/<int:pk>/check-available/', views.RpmPackageCheckAvailableView.as_view(), name='package_check_available'),

    # Builds
    path('rpms/builds/<int:pk>/', views.RpmBuildDetailView.as_view(), name='build_detail'),
    path('rpms/builds/<int:pk>/logs/', views.RpmBuildLogsApiView.as_view(), name='build_logs'),
    path('rpms/builds/<int:pk>/retry/', views.RpmBuildRetryView.as_view(), name='build_retry'),
    path('rpms/builds/<int:pk>/cancel/', views.RpmBuildCancelView.as_view(), name='build_cancel'),
    path('rpms/builds/<int:pk>/delete/', views.RpmBuildDeleteView.as_view(), name='build_delete'),

    # Spec file scanner
    path('rpms/scan-spec-files/', views.RpmScanSpecFilesView.as_view(), name='scan_spec_files'),
    path('rpms/scan-branches/', views.RpmScanBranchesView.as_view(), name='scan_branches'),

    # Distributions
    path('rpms/distributions/', views.RpmDistributionListView.as_view(), name='distribution_list'),
    path('rpms/distributions/sync/', views.RpmDistributionSyncView.as_view(), name='distribution_sync'),
    path('rpms/distributions/<int:pk>/toggle/', views.RpmDistributionToggleView.as_view(), name='distribution_toggle'),
    path('rpms/distributions/<int:pk>/sync-repos/', views.RpmDistributionSyncReposView.as_view(), name='distribution_sync_repos'),
    path('rpms/distributions/repos/<int:repo_pk>/toggle/', views.RpmRepositoryToggleView.as_view(), name='repository_toggle'),

    # Package-level signing key assignment
    path('rpms/<int:pkg_pk>/signing-key/<int:dist_pk>/', views.RpmPackageSigningKeyView.as_view(), name='package_signing_key'),

    # Satellite / Katello destinations
    path('rpms/destinations/', views.RpmDestinationListView.as_view(), name='destination_list'),
    path('rpms/destinations/add-server/', views.RpmSatelliteServerCreateView.as_view(), name='server_create'),
    path('rpms/destinations/servers/<int:pk>/delete/', views.RpmSatelliteServerDeleteView.as_view(), name='server_delete'),
    path('rpms/destinations/servers/<int:server_pk>/add-repo/', views.RpmSatelliteRepositoryAddView.as_view(), name='repo_add'),
    path('rpms/destinations/repos/<int:pk>/delete/', views.RpmSatelliteRepositoryDeleteView.as_view(), name='repo_delete'),
    path('rpms/destinations/api/orgs/', views.RpmSatelliteFetchOrgsView.as_view(), name='fetch_orgs'),
    path('rpms/destinations/api/products/', views.RpmSatelliteFetchProductsView.as_view(), name='fetch_products'),
    path('rpms/destinations/api/repos/', views.RpmSatelliteFetchReposView.as_view(), name='fetch_repos'),
    path('rpms/<int:pk>/assign-destination/', views.RpmPackageAssignDestinationView.as_view(), name='assign_destination'),
    path('rpms/destinations/<int:pk>/remove/', views.RpmPackageRemoveDestinationView.as_view(), name='remove_destination'),
]
