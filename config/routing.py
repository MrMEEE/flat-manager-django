"""
WebSocket URL routing for flat-manager project.
"""
from django.urls import path
from apps.flatpak.consumers import BuildStatusConsumer, RepoStatusConsumer, NotificationsConsumer

websocket_urlpatterns = [
    path('ws/builds/<int:build_id>/', BuildStatusConsumer.as_asgi()),
    path('ws/repos/<int:repo_id>/', RepoStatusConsumer.as_asgi()),
    path('ws/notifications/', NotificationsConsumer.as_asgi()),
]
