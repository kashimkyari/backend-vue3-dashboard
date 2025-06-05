# utils/notifications.py
from flask import current_app
import os
import logging
import requests
from models import Stream, User

# Initialize logger
logger = logging.getLogger(__name__)

# Cache for agent usernames
agent_cache = {}

def forward_to_main_app(event, data, namespace='/notifications'):
    """Forward a SocketIO event to the main app's SocketIO server (used by monitoring app)"""
    main_app_url = os.getenv('MAIN_APP_URL', current_app.config.get('MAIN_APP_URL', 'http://localhost:5000'))
    try:
        response = requests.post(
            f"{main_app_url}/api/forward-socketio",
            json={
                'event': event,
                'data': data,
                'namespace': namespace
            },
            timeout=10  # Increased timeout
        )
        if response.status_code == 200:
            logger.info(f"Forwarded {event} to main app: {data}")
            return True
        else:
            logger.error(f"Failed to forward {event} to main app: {response.status_code} - {response.text}")
            return False
    except requests.RequestException as e:
        logger.error(f"Error forwarding {event} to main app: {str(e)}")
        return False

def emit_notification(notification_data, forward_to_main=False):
    """Emit a notification to all connected clients"""
    namespace = '/notifications'
    
    try:
        socketio.emit('notification', notification_data, namespace=namespace)
        logger.info(f"Emitted notification: {notification_data.get('event_type', 'unknown')}")
        
        if forward_to_main:
            forward_to_main_app('notification', notification_data, namespace)

        # Emit to admin users specifically
        emit_role_notification(notification_data, 'admin', forward_to_main)

        # If notification is for a specific stream, emit to its room
        stream_url = notification_data.get('room_url')
        if stream_url:
            stream = Stream.query.filter_by(room_url=stream_url).first()
            if stream:
                emit_stream_notification(notification_data, stream.id, forward_to_main)

                # If the notification is assigned to a specific agent
                assigned_agent = notification_data.get('assigned_agent')
                if assigned_agent and assigned_agent != 'Unassigned':
                    agent = User.query.filter_by(username=assigned_agent).first()
                    if agent:
                        emit_agent_notification(notification_data, agent.id, forward_to_main)

        return True
    except Exception as e:
        logger.error(f"Error emitting notification: {str(e)}")
        if forward_to_main:
            forward_to_main_app('notification', notification_data, namespace)
        return False

def emit_notification_update(notification_id, update_type='read', forward_to_main=False):
    """Emit a notification update (read, deleted, etc.) to all connected clients"""
    namespace = '/notifications'
    data = {'id': notification_id, 'type': update_type}

    try:
        socketio.emit('notification_update', data, namespace=namespace)
        logger.info(f"Emitted notification update: {update_type} for {notification_id}")
        
        if forward_to_main:
            forward_to_main_app('notification_update', data, namespace)
        return True
    except Exception as e:
        logger.error(f"Error emitting notification update: {str(e)}")
        if forward_to_main:
            forward_to_main_app('notification_update', data, namespace)
        return False

def emit_stream_update(stream_data, forward_to_main=False):
    """Emit a stream update to all connected clients"""
    namespace = '/notifications'

    try:
        socketio.emit('stream_update', stream_data, namespace=namespace)
        logger.info(f"Emitted stream update for stream ID: {stream_data.get('id')}")
        
        if forward_to_main:
            forward_to_main_app('stream_update', stream_data, namespace)
        return True
    except Exception as e:
        logger.error(f"Error emitting stream update: {str(e)}")
        if forward_to_main:
            forward_to_main_app('stream_update', stream_data, namespace)
        return False

def emit_message_update(message_data, forward_to_main=False):
    """Emit a message notification to specific recipients"""
    namespace = '/notifications'

    try:
        receiver_id = message_data.get('receiver_id')
        if receiver_id:
            socketio.emit('new_message', message_data, room=f"user_{receiver_id}", namespace=namespace)
            logger.info(f"Emitted message notification to user: {receiver_id}")

        sender_id = message_data.get('sender_id')
        if sender_id:
            socketio.emit('message_sent', message_data, room=f"user_{sender_id}", namespace=namespace)
        
        if forward_to_main:
            forward_to_main_app('message_update', message_data, namespace)
        return True
    except Exception as e:
        logger.error(f"Error emitting message notification: {str(e)}")
        if forward_to_main:
            forward_to_main_app('message_update', message_data, namespace)
        return False

def emit_assignment_update(assignment_data, forward_to_main=False):
    """Emit an assignment update to affected agents"""
    namespace = '/notifications'

    try:
        agent_id = assignment_data.get('agent_id')
        if agent_id:
            socketio.emit('assignment_update', assignment_data, room=f"user_{agent_id}", namespace=namespace)
            logger.info(f"Emitted assignment notification to agent: {agent_id}")
        
        if forward_to_main:
            forward_to_main_app('assignment_update', assignment_data, namespace)
        return True
    except Exception as e:
        logger.error(f"Error emitting assignment notification: {str(e)}")
        if forward_to_main:
            forward_to_main_app('assignment_update', assignment_data, namespace)
        return False

def emit_stream_notification(notification_data, stream_id, forward_to_main=False):
    """Emit a notification to users subscribed to a specific stream"""
    namespace = '/notifications'

    try:
        stream_room = f"stream_{stream_id}"
        socketio.emit('notification', notification_data, room=stream_room, namespace=namespace)
        logger.info(f"Emitted notification to stream room: {stream_room}")
        
        if forward_to_main:
            forward_to_main_app('stream_notification', {'data': notification_data, 'stream_id': stream_id}, namespace)
        return True
    except Exception as e:
        logger.error(f"Error emitting stream notification: {str(e)}")
        if forward_to_main:
            forward_to_main_app('stream_notification', {'data': notification_data, 'stream_id': stream_id}, namespace)
        return False

def emit_role_notification(notification_data, role, forward_to_main=False):
    """Emit a notification to all users with a specific role"""
    namespace = '/notifications'

    try:
        role_room = f"role_{role}"
        socketio.emit('notification', notification_data, room=role_room, namespace=namespace)
        logger.info(f"Emitted notification to role room: {role_room}")
        
        if forward_to_main:
            forward_to_main_app('role_notification', {'data': notification_data, 'role': role}, namespace)
        return True
    except Exception as e:
        logger.error(f"Error emitting role notification: {str(e)}")
        if forward_to_main:
            forward_to_main_app('role_notification', {'data': notification_data, 'role': role}, namespace)
        return False

def emit_agent_notification(notification_data, agent_id, forward_to_main=False):
    """Emit a notification to a specific agent"""
    namespace = '/notifications'

    try:
        agent_room = f"user_{agent_id}"
        socketio.emit('notification', notification_data, room=agent_room, namespace=namespace)
        logger.info(f"Emitted notification to agent: {agent_id}")
        
        if forward_to_main:
            forward_to_main_app('agent_notification', {'data': notification_data, 'agent_id': agent_id}, namespace)
        return True
    except Exception as e:
        logger.error(f"Error emitting agent notification: {str(e)}")
        if forward_to_main:
            forward_to_main_app('agent_notification', {'data': notification_data, 'agent_id': agent_id}, namespace)
        return False