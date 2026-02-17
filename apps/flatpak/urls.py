from django.urls import path
from . import views

app_name = 'flatpak'

urlpatterns = [
    path('gpg-keys/', views.GPGKeyListView.as_view(), name='gpgkey_list'),
    path('gpg-keys/generate/', views.gpgkey_generate, name='gpgkey_generate'),
    path('gpg-keys/import/', views.gpgkey_import, name='gpgkey_import'),
    path('gpg-keys/<int:pk>/', views.GPGKeyDetailView.as_view(), name='gpgkey_detail'),
    path('gpg-keys/<int:pk>/delete/', views.GPGKeyDeleteView.as_view(), name='gpgkey_delete'),
    path('gpg-keys/<int:pk>/download/', views.GPGKeyDownloadView.as_view(), name='gpgkey_download'),
    path('repos/', views.RepositoryListView.as_view(), name='repo_list'),
    path('repos/<int:pk>/', views.RepositoryDetailView.as_view(), name='repo_detail'),
    path('repos/create/', views.RepositoryCreateView.as_view(), name='repo_create'),
    path('repos/<int:pk>/edit/', views.RepositoryUpdateView.as_view(), name='repo_edit'),
    path('repos/<int:pk>/delete/', views.RepositoryDeleteView.as_view(), name='repo_delete'),
    path('repos/<int:repo_pk>/subsets/create/', views.RepositorySubsetCreateView.as_view(), name='subset_create'),
    path('subsets/<int:pk>/edit/', views.RepositorySubsetUpdateView.as_view(), name='subset_edit'),
    path('subsets/<int:pk>/delete/', views.RepositorySubsetDeleteView.as_view(), name='subset_delete'),
    path('builds/', views.BuildListView.as_view(), name='build_list'),
    path('builds/create/', views.BuildCreateView.as_view(), name='build_create'),
    path('builds/<int:pk>/', views.BuildDetailView.as_view(), name='build_detail'),
    path('builds/<int:pk>/edit/', views.BuildUpdateView.as_view(), name='build_edit'),
    path('builds/<int:pk>/delete/', views.BuildDeleteView.as_view(), name='build_delete'),
    path('builds/<int:pk>/retry/', views.BuildRetryView.as_view(), name='build_retry'),
    path('dependencies/', views.dependencies_list, name='dependencies_list'),
]
