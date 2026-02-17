import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


class BuildStatusConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time build status updates.
    """
    async def connect(self):
        self.build_id = self.scope['url_route']['kwargs'].get('build_id')
        self.room_group_name = f'build_{self.build_id}' if self.build_id else 'builds'
        
        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
    
    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        """Receive message from WebSocket"""
        text_data_json = json.loads(text_data)
        message = text_data_json.get('message', '')
        
        # Send message to room group
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'build_message',
                'message': message
            }
        )
    
    async def build_message(self, event):
        """Receive message from room group"""
        message = event['message']
        
        # Send message to WebSocket
        await self.send(text_data=json.dumps({
            'message': message
        }))
    
    async def build_status_update(self, event):
        """Send build status update to WebSocket"""
        await self.send(text_data=json.dumps({
            'type': 'build_status_update',
            'build_id': event.get('build_id'),
            'status': event.get('status'),
            'message': event.get('message', ''),
            'timestamp': event.get('timestamp')
        }))


class NotificationsConsumer(AsyncWebsocketConsumer):
    """
    General notifications consumer for all real-time updates.
    All authenticated users connect to this for global notifications.
    """
    async def connect(self):
        # General notifications room for all users
        self.room_group_name = 'notifications'
        
        # Also join builds room for build updates
        await self.channel_layer.group_add(
            'builds',
            self.channel_name
        )
        
        # Join general notifications
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
    
    async def disconnect(self, close_code):
        # Leave room groups
        await self.channel_layer.group_discard(
            'builds',
            self.channel_name
        )
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def notification_message(self, event):
        """Send notification to WebSocket"""
        await self.send(text_data=json.dumps(event))
    
    async def build_status_update(self, event):
        """Forward build status updates"""
        await self.send(text_data=json.dumps({
            'type': 'build_status_update',
            'build_id': event.get('build_id'),
            'status': event.get('status'),
            'message': event.get('message', ''),
            'timestamp': event.get('timestamp'),
            'repository_id': event.get('repository_id')
        }))
    
    async def build_log_update(self, event):
        """Forward build log updates"""
        await self.send(text_data=json.dumps({
            'type': 'build_log_update',
            'build_id': event.get('build_id'),
            'log': event.get('log')
        }))
    
    async def repository_updated(self, event):
        """Forward repository updates"""
        await self.send(text_data=json.dumps({
            'type': 'repository_updated',
            'repository_id': event.get('repository_id'),
            'message': event.get('message', '')
        }))


class RepoStatusConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for repository status updates.
    """
    async def connect(self):
        self.repo_id = self.scope['url_route']['kwargs'].get('repo_id')
        self.room_group_name = f'repo_{self.repo_id}'
        
        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
    
    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def repo_update(self, event):
        """Send repository update to WebSocket"""
        await self.send(text_data=json.dumps({
            'type': 'repo_update',
            'repo_id': event['repo_id'],
            'message': event.get('message', ''),
            'timestamp': event.get('timestamp', ''),
        }))
