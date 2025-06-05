#!/usr/bin/env python3
"""
main.py - Flask application entry point
"""
import gevent.monkey
gevent.monkey.patch_all()

import logging
import os
from dotenv import load_dotenv
from flask import jsonify, request
from werkzeug.security import generate_password_hash
import secrets
import string
from config import create_app, configure_ssl_context
from extensions import db, socketio
from models import User

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

load_dotenv()

# Initialize Flask app
app = create_app()

with app.app_context():
    # Set communication URLs
    app.config['MONITOR_APP_URL'] = app.config.get('MONITOR_APP_URL', 
        f"https://{app.config.get('MONITOR_APP_HOST', 'localhost')}:{app.config.get('MONITOR_APP_PORT', 5001)}")
    
    # Initialize communication service
    from services.communication_service import communication_service
    communication_service.app = app

# Database initialization
def initialize_database():
    """Initialize database and create default admin user."""
    with app.app_context():
        try:
            db.create_all()
            logger.info("Database tables initialized")
            admin_exists = User.query.filter_by(role='admin').first()
            if not admin_exists:
                admin_username = os.getenv('DEFAULT_ADMIN_USERNAME', 'admin')
                admin_password = os.getenv('DEFAULT_ADMIN_PASSWORD')
                admin_email = os.getenv('DEFAULT_ADMIN_EMAIL', 'admin@example.com')
                if not admin_password:
                    chars = string.ascii_letters + string.digits + string.punctuation
                    admin_password = ''.join(secrets.choice(chars) for _ in range(16))
                    logger.warning(f"Admin password not found. Generated: {admin_password}")
                    logger.warning("SAVE THIS PASSWORD AND SET ENV VARIABLES!")
                admin_user = User(
                    username=admin_username,
                    password=generate_password_hash(admin_password),
                    role='admin',
                    email=admin_email,
                    receive_updates=True
                )
                db.session.add(admin_user)
                db.session.commit()
                logger.info("Default admin user created")
            else:
                logger.info("Admin user already exists")
        except Exception as e:
            logger.error(f"DB init failed: {str(e)}")
            raise

# Forward SocketIO endpoint
@app.route('/api/forward-socketio', methods=['POST'])
def forward_socketio():
    """Handle forwarded SocketIO events from the monitoring app."""
    try:
        data = request.get_json()
        if not data or 'event' not in data or 'data' not in data or 'namespace' not in data:
            return jsonify({'error': 'Invalid request data'}), 400
        
        socketio.emit(data['event'], data['data'], namespace=data['namespace'])
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        logger.error(f"Error forwarding socketio event: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Main execution
if __name__ == "__main__":
    try:
        initialize_database()
        ssl_config = configure_ssl_context()
        debug_mode = os.getenv('FLASK_DEBUG', 'true').lower() == 'true'
        
        # Prepare socketio arguments
        socketio_kwargs = {
            'app': app,
            'host': '0.0.0.0',
            'port': int(os.getenv('PORT', 5000)),
            'debug': debug_mode,
            'use_reloader': debug_mode,
            'allow_unsafe_werkzeug': True
        }
        
        # Handle SSL configuration
        if ssl_config:
            if isinstance(ssl_config, tuple) and len(ssl_config) == 2:
                # SSL config returned as (cert_file, key_file) tuple
                cert_file, key_file = ssl_config
                socketio_kwargs['certfile'] = cert_file
                socketio_kwargs['keyfile'] = key_file
                server_mode = "HTTPS"
                logger.info(f"SSL enabled with cert: {cert_file}, key: {key_file}")
            elif hasattr(ssl_config, 'wrap_socket'):
                # SSL config returned as SSL context object
                socketio_kwargs['ssl_context'] = ssl_config
                server_mode = "HTTPS"
                logger.info("SSL enabled with SSL context object")
            else:
                # Invalid SSL config, fall back to HTTP
                logger.warning("Invalid SSL configuration, falling back to HTTP")
                server_mode = "HTTP"
        else:
            server_mode = "HTTP"
        
        logger.info(f"Starting server in {server_mode} mode with debug={'enabled' if debug_mode else 'disabled'}")
        
        socketio.run(**socketio_kwargs)
    except Exception as e:
        logger.error(f"Application startup failed: {str(e)}")
        raise