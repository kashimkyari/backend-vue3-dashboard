# routes/socketio_routes.py
from flask import Blueprint
from monitor_extensions import socketio
from flask_socketio import emit
import logging

socketio_bp = Blueprint('socketio', __name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@socketio.on('connect')
def handle_connect():
    """Handle client connection to SocketIO."""
    logger.info("Client connected to SocketIO")
    emit('connection', {'message': 'Connected to monitoring service'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection from SocketIO."""
    logger.info("Client disconnected from SocketIO")

@socketio.on('subscribe_stream')
def handle_subscribe_stream(data):
    """Handle subscription to stream updates."""
    stream_id = data.get('stream_id')
    if stream_id:
        logger.info(f"Client subscribed to stream {stream_id}")
        emit('stream_subscription', {'stream_id': stream_id, 'status': 'subscribed'})
    else:
        logger.error("Missing stream_id in subscribe_stream")
        emit('error', {'error': 'Missing stream_id'})

def emit_stream_update(data, forward_to_main=True):
    """Emit stream update to all connected clients."""
    try:
        logger.info(f"Emitting stream update: {data}")
        socketio.emit('stream_update', data)
        if forward_to_main:
            from flask import current_app
            main_app_url = current_app.config.get('MAIN_APP_URL')
            if main_app_url:
                import requests
                response = requests.post(
                    f"{main_app_url}/api/forward-socketio",
                    json={
                        'event': 'stream_update',
                        'data': data,
                        'namespace': '/monitor'
                    },
                    timeout=5
                )
                response.raise_for_status()
                logger.info("Forwarded stream update to main app")
    except Exception as e:
        logger.error(f"Error emitting stream update: {str(e)}")

def emit_notification(data, forward_to_main=True):
    """Emit notification to all connected clients."""
    try:
        logger.info(f"Emitting notification: {data}")
        socketio.emit('notification', data)
        if forward_to_main:
            from flask import current_app
            main_app_url = current_app.config.get('MAIN_APP_URL')
            if main_app_url:
                import requests
                response = requests.post(
                    f"{main_app_url}/api/forward-socketio",
                    json={
                        'event': 'notification',
                        'data': data,
                        'namespace': '/monitor'
                    },
                    timeout=5
                )
                response.raise_for_status()
                logger.info("Forwarded notification to main app")
    except Exception as e:
        logger.error(f"Error emitting notification: {str(e)}")

def emit_notification_update(notification_id, action, forward_to_main=True):
    """Emit notification update to all connected clients."""
    try:
        logger.info(f"Emitting notification update: id={notification_id}, action={action}")
        socketio.emit('notification_update', {'id': notification_id, 'action': action})
        if forward_to_main:
            from flask import current_app
            main_app_url = current_app.config.get('MAIN_APP_URL')
            if main_app_url:
                import requests
                response = requests.post(
                    f"{main_app_url}/api/forward-socketio",
                    json={
                        'event': 'notification_update',
                        'data': {'id': notification_id, 'type': action},
                        'namespace': '/monitor'
                    },
                    timeout=5
                )
                response.raise_for_status()
                logger.info("Forwarded notification update to main app")
    except Exception as e:
        logger.error(f"Error emitting notification update: {str(e)}")

def emit_message_update(data, forward_to_main=True):
    """Emit message update to all connected clients."""
    try:
        logger.info(f"Emitting message update: {data}")
        socketio.emit('message_update', data)
        if forward_to_main:
            from flask import current_app
            main_app_url = current_app.config.get('MAIN_APP_URL')
            if main_app_url:
                import requests
                response = requests.post(
                    f"{main_app_url}/api/forward-socketio",
                    json={
                        'event': 'message_update',
                        'data': data,
                        'namespace': '/monitor'
                    },
                    timeout=5
                )
                response.raise_for_status()
                logger.info("Forwarded message update to main app")
    except Exception as e:
        logger.error(f"Error emitting message update: {str(e)}")