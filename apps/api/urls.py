from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'users', views.UserViewSet)
router.register(r'profiles', views.UserProfileViewSet)
router.register(r'gpg-keys', views.GPGKeyViewSet)
router.register(r'repositories', views.RepositoryViewSet)
router.register(r'repository-subsets', views.RepositorySubsetViewSet)
router.register(r'builds', views.BuildViewSet)
router.register(r'artifacts', views.BuildArtifactViewSet)
router.register(r'tokens', views.TokenViewSet)

app_name = 'api'

urlpatterns = [
    path('', include(router.urls)),
    path('auth/', include('rest_framework.urls')),
    path('git-branches/', views.git_branches, name='git_branches'),
]
