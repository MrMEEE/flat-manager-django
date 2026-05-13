from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    path('dashboard/stats/', views.DashboardStatsApiView.as_view(), name='dashboard_stats'),

    # User management
    path('users/', views.UserListView.as_view(), name='user_list'),
    path('users/create/', views.UserCreateView.as_view(), name='user_create'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='user_detail'),
    path('users/<int:pk>/edit/', views.UserUpdateView.as_view(), name='user_edit'),
    path('users/<int:pk>/password/', views.UserSetPasswordView.as_view(), name='user_set_password'),
    path('users/<int:pk>/roles/', views.UserRoleView.as_view(), name='user_roles'),

    # LDAP sources
    path('ldap/', views.LDAPSourceListView.as_view(), name='ldap_list'),
    path('ldap/create/', views.LDAPSourceCreateView.as_view(), name='ldap_create'),
    path('ldap/<int:pk>/', views.LDAPSourceDetailView.as_view(), name='ldap_detail'),
    path('ldap/<int:pk>/edit/', views.LDAPSourceUpdateView.as_view(), name='ldap_edit'),
    path('ldap/<int:pk>/delete/', views.LDAPSourceDeleteView.as_view(), name='ldap_delete'),

    # LDAP group mappings
    path('ldap/<int:source_pk>/mappings/add/', views.LDAPGroupMappingCreateView.as_view(), name='ldap_mapping_add'),
    path('ldap/<int:source_pk>/mappings/<int:pk>/delete/', views.LDAPGroupMappingDeleteView.as_view(), name='ldap_mapping_delete'),

    path('profile/', views.ProfileView.as_view(), name='profile'),
]
