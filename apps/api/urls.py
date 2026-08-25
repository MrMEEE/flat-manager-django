from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from . import flathub

router = DefaultRouter()
router.register(r'users', views.UserViewSet)
router.register(r'profiles', views.UserProfileViewSet)
router.register(r'gpg-keys', views.GPGKeyViewSet)
router.register(r'repositories', views.RepositoryViewSet)
router.register(r'repository-subsets', views.RepositorySubsetViewSet)
router.register(r'packages', views.PackageViewSet)
router.register(r'builds', views.BuildViewSet, basename='build')  # Build history
router.register(r'artifacts', views.BuildArtifactViewSet)
router.register(r'tokens', views.TokenViewSet)

app_name = 'api'

urlpatterns = [
    path('', include(router.urls)),
    path('v2/status', flathub.StatusView.as_view(), name='flathub-status'),
    path('v2/appstream', flathub.AppstreamView.as_view(), name='flathub-appstream'),
    path('v2/appstream/<str:app_id>', flathub.AppstreamView.as_view(), name='flathub-appstream-detail'),
    path('v2/appstream/<str:app_id>/metadata', flathub.AppMetadataView.as_view(), name='flathub-app-metadata'),
    path('v2/summary/<str:app_id>', flathub.SummaryView.as_view(), name='flathub-summary'),
    path('v2/stats/', flathub.AppStatsView.as_view(), name='flathub-stats'),
    path('v2/stats/<str:app_id>', flathub.AppStatsView.as_view(), name='flathub-app-stats'),
    path('v2/usage/popular', flathub.UsageView.as_view(), name='flathub-usage-popular'),
    path('v2/addon/<str:app_id>', flathub.AddonView.as_view(), name='flathub-addon'),
    path('v2/platforms', flathub.PlatformView.as_view(), name='flathub-platforms'),
    path('v2/runtimes', flathub.RuntimeView.as_view(), name='flathub-runtimes'),
    path('v2/is-fullscreen-app/<str:app_id>', flathub.FullscreenAppView.as_view(), name='flathub-fullscreen-app'),
    path('v2/search', flathub.SearchView.as_view(), name='flathub-search'),
    path('v2/feed/<str:feed>', flathub.FeedView.as_view(), name='flathub-feed'),
    path('v2/collection/category', flathub.NamedCollectionView.as_view(), {'collection': 'category'}, name='flathub-category-list'),
    path('v2/collection/category/<str:category>/subcategories', flathub.CategorySubcategoriesView.as_view(), name='flathub-category-subcategories'),
    path('v2/collection/category/<str:value>', flathub.NamedCollectionView.as_view(), {'collection': 'category'}, name='flathub-category'),
    path('v2/collection/developer', flathub.NamedCollectionView.as_view(), {'collection': 'developer'}, name='flathub-developer-list'),
    path('v2/collection/developer/<str:value>', flathub.NamedCollectionView.as_view(), {'collection': 'developer'}, name='flathub-developer'),
    path('v2/collection/keyword', flathub.NamedCollectionView.as_view(), {'collection': 'keyword'}, name='flathub-keyword'),
    path('v2/collection/keywords', flathub.NamedCollectionView.as_view(), {'collection': 'keywords'}, name='flathub-keywords'),
    path('v2/collection/<str:collection>', flathub.PackageCollectionView.as_view(), name='flathub-collection'),
    path('v2/quality-moderation/status', flathub.QualityStatusView.as_view(), name='flathub-quality-status'),
    path('v2/quality-moderation/stats-by-category', flathub.QualityStatsView.as_view(), name='flathub-quality-stats'),
    path('v2/quality-moderation/passing-apps', flathub.QualityAppsView.as_view(), {'result': 'passing-apps'}, name='flathub-quality-passing'),
    path('v2/quality-moderation/failed-by-guideline', flathub.QualityAppsView.as_view(), {'result': 'failed-by-guideline'}, name='flathub-quality-failed'),
    path('v2/quality-moderation/app-pick-recommendations', flathub.QualityAppsView.as_view(), {'result': 'app-pick-recommendations'}, name='flathub-quality-recommendations'),
    path('v2/quality-moderation/<str:app_id>/status', flathub.QualityAppView.as_view(), name='flathub-quality-app-status'),
    path('v2/quality-moderation/<str:app_id>/fullscreen', flathub.QualityAppActionView.as_view(), {'action': 'fullscreen'}, name='flathub-quality-fullscreen'),
    path('v2/quality-moderation/<str:app_id>/request-review', flathub.QualityAppActionView.as_view(), {'action': 'request-review'}, name='flathub-quality-request-review'),
    path('v2/quality-moderation/<str:app_id>', flathub.QualityAppView.as_view(), name='flathub-quality-app'),
    path('v2/year-in-review/<int:year>', flathub.YearInReviewView.as_view(), name='flathub-year-in-review'),
    path('v2/app-picks/admin/curated-app-selections', flathub.AppPickAdminView.as_view(), name='flathub-app-picks-admin'),
    path('v2/app-picks/admin/curated-app-selection-themes', flathub.AppPickThemeView.as_view(), name='flathub-app-pick-themes'),
    path('v2/app-picks/admin/curated-app-selections/<int:selection_id>', flathub.AppPickSelectionDetailView.as_view(), name='flathub-app-pick-detail'),
    path('v2/app-picks/admin/apps-of-the-week/<str:date>', flathub.AdminAppPickView.as_view(), {'kind': 'week'}, name='flathub-admin-apps-of-week'),
    path('v2/app-picks/app-of-the-day', flathub.AppPickDateView.as_view(), {'kind': 'day'}, name='flathub-app-of-day-current'),
    path('v2/app-picks/app-of-the-week', flathub.AppPickDateView.as_view(), {'kind': 'week'}, name='flathub-app-of-week-current'),
    path('v2/app-picks/curated-app-selections/<str:date>', flathub.AppPickView.as_view(), {'kind': 'curated'}, name='flathub-curated-picks'),
    path('v2/app-picks/app-of-the-day/<str:date>', flathub.AppPickView.as_view(), {'kind': 'day'}, name='flathub-app-of-day'),
    path('v2/app-picks/apps-of-the-week/<str:date>', flathub.AppPickView.as_view(), {'kind': 'week'}, name='flathub-apps-of-week'),
    path('v2/moderation/submit_review_request', flathub.SubmitModerationView.as_view(), name='flathub-submit-moderation'),
    path('v2/moderation/apps', flathub.ModerationAppsView.as_view(), name='flathub-moderation-apps'),
    path('v2/moderation/apps/<str:app_id>', flathub.ModerationAppsView.as_view(), name='flathub-moderation-app'),
    path('v2/moderation/requests/<int:request_id>/review', flathub.ReviewModerationView.as_view(), name='flathub-review-moderation'),
    path('auth/', include('rest_framework.urls')),
    path('git-branches/', views.git_branches, name='git_branches'),
]
