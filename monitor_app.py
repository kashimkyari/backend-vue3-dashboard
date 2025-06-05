#!/usr/bin/env python3
"""
monitor_app.py - Enhanced standalone Flask application for livestream monitoring
"""
import gevent.monkey
gevent.monkey.patch_all()

import logging
import os
import time
from dotenv import load_dotenv
from config import create_app, configure_ssl_context
from extensions import db, socketio
from monitoring import start_notification_monitor, initialize_monitoring
from routes.monitor_routes import monitor_bp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

load_dotenv()

# Initialize Flask app with monitor blueprint
app = create_app(blueprint=monitor_bp)

# Ensure the actual app instance is used
with app.app_context():
    # Set communication URLs
    app.config['MAIN_APP_URL'] = app.config.get('MAIN_APP_URL',
        f"{app.config.get('MAIN_APP_PROTOCOL', 'https')}://{app.config.get('MAIN_APP_HOST', 'localhost')}:{app.config.get('MAIN_APP_PORT', 5000)}")
    
    # Initialize communication service
    from services.communication_service import communication_service
    communication_service.app = app

def startup_monitoring():
    logger.info("Starting monitoring initialization...")
    try:
        with app.app_context():
            max_db_retries = 5
            for i in range(max_db_retries):
                try:
                    from models import Stream
                    Stream.query.limit(1).all()
                    logger.info("Database connection verified")
                    break
                except Exception as e:
                    logger.warning(f"Database not ready, attempt {i+1}/{max_db_retries}: {e}")
                    if i == max_db_retries - 1:
                        logger.error(f"Database not ready after {max_db_retries} attempts: {e}, proceeding without db")
                    time.sleep(5)
            
            logger.info("Initializing monitoring components...")
            initialize_monitoring(app=app)  # Pass the actual app instance
            
            logger.info("Starting notification monitoring...")
            start_notification_monitor(app=app)  # Pass the actual app instance
            
            logger.info("Monitoring initialization completed successfully")
    except Exception as e:
        logger.error(f"Failed to initialize monitoring: {e}")
        logger.info("Server will continue running")

# Main execution
if __name__ == "__main__":
    try:
        ssl_config = configure_ssl_context()
        debug_mode = os.getenv('FLASK_DEBUG', 'true').lower() == 'true'
        
        # Prepare socketio arguments
        socketio_kwargs = {
            'app': app,
            'host': '0.0.0.0',
            'port': app.config.get('MONITOR_APP_PORT', 5001),
            'debug': debug_mode,
            'use_reloader': debug_mode,
            'allow_unsafe_werkzeug': True
        }
        
        # Handle SSL configuration
        if ssl_config:
            if isinstance(ssl_config, tuple) and len(ssl_config) == 2:
                cert_file, key_file = ssl_config
                socketio_kwargs['certfile'] = cert_file
                socketio_kwargs['keyfile'] = key_file
                server_mode = "HTTPS"
                logger.info(f"SSL enabled with cert: {cert_file}, key: {key_file}")
            elif hasattr(ssl_config, 'wrap_socket'):
                socketio_kwargs['ssl_context'] = ssl_config
                server_mode = "HTTPS"
                logger.info("SSL enabled with SSL context object")
            else:
                logger.warning("Invalid SSL configuration, falling back to HTTP")
                server_mode = "HTTP"
        else:
            server_mode = "HTTP"
        
        logger.info(f"Starting monitoring server in {server_mode} mode with debug={'enabled' if debug_mode else 'disabled'}")
        
        # Initialize monitoring immediately
        startup_monitoring()
        
        logger.info(f"Server starting on port {app.config.get('MONITOR_APP_PORT', 5001)}")
        socketio.run(**socketio_kwargs)
        
    except Exception as e:
        logger.error(f"Monitoring application startup failed: {str(e)}")
        raise