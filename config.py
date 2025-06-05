import os
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
from extensions import db, socketio, redis_service
from services.notification_service import NotificationService
from sqlalchemy import event
from sqlalchemy.engine import Engine
import logging

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class Config:
    """Base configuration for all environments."""
    # ─── Secret & Security ───────────────────────────────────────────────
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'please-set-a-secure-key')
    SESSION_COOKIE_SECURE = os.getenv('ENABLE_SSL', 'false').lower() == 'true'
    REMEMBER_COOKIE_SECURE = os.getenv('ENABLE_SSL', 'false').lower() == 'true'

    # ─── Database ────────────────────────────────────────────────────────
    if os.getenv('DATABASE_URL') and 'supabase' in os.getenv('DATABASE_URL'):
        db_uri = os.getenv('DATABASE_URL')
        masked_uri = db_uri[:db_uri.find('://') + 3] + '****:****@' + db_uri[db_uri.find('@') + 1:]
        logger.info(f"Using Supabase database: {masked_uri}")
        
        ssl_params = []
        if 'sslmode' not in db_uri:
            ssl_params.append('sslmode=require')
        
        root_cert_path = os.getenv('PG_ROOT_CERT_PATH')
        if root_cert_path and os.path.exists(root_cert_path):
            logger.info(f"Using SSL root certificate: {root_cert_path}")
            ssl_params.append(f'sslrootcert={root_cert_path}')
        else:
            logger.info("Using SSL without certificate verification")
        
        if ssl_params:
            connector = '&' if '?' in db_uri else '?'
            db_uri = f"{db_uri}{connector}{'&'.join(ssl_params)}"
        
        SQLALCHEMY_DATABASE_URI = db_uri
    else:
        SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 20,
        'pool_recycle': 300,
        'pool_pre_ping': True,
        'max_overflow': 10,
        'pool_use_lifo': True,
        'pool_timeout': 20,
        'connect_args': {
            'keepalives': 1,
            'keepalives_idle': 30,
            'keepalives_interval': 10,
            'keepalives_count': 5,
            'connect_timeout': 5,
            'application_name': 'jetcamstudio_app'
        },
        'pool_logging_name': 'jetcamstudio_pool',
    }

    # Main app configuration
    MAIN_APP_HOST = os.getenv('MAIN_APP_HOST', 'localhost')
    MAIN_APP_PORT = int(os.getenv('MAIN_APP_PORT', 5000))
    MAIN_APP_PROTOCOL = os.getenv('MAIN_APP_PROTOCOL', 'https')

    # Monitor app configuration
    MONITOR_APP_HOST = os.getenv('MONITOR_APP_HOST', 'localhost')
    MONITOR_APP_PORT = int(os.getenv('MONITOR_APP_PORT', 5001))
    MONITOR_APP_PROTOCOL = os.getenv('MONITOR_APP_PROTOCOL', 'https')
    
    # Communication timeouts and retries
    INTER_SERVICE_TIMEOUT = int(os.getenv('INTER_SERVICE_TIMEOUT', 30))
    INTER_SERVICE_RETRIES = int(os.getenv('INTER_SERVICE_RETRIES', 3))
    INTER_SERVICE_RETRY_DELAY = float(os.getenv('INTER_SERVICE_RETRY_DELAY', 1.0))
    
    @classmethod
    def get_main_app_url(cls):
        """Get the main app URL."""
        return f"{cls.MAIN_APP_PROTOCOL}://{cls.MAIN_APP_HOST}:{cls.MAIN_APP_PORT}"
    
    @classmethod
    def get_monitor_app_url(cls):
        """Get the monitor app URL."""
        return f"{cls.MONITOR_APP_PROTOCOL}://{cls.MONITOR_APP_HOST}:{cls.MONITOR_APP_PORT}"

    # ─── Redis Configuration ─────────────────────────────────────────────
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
    REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)
    REDIS_DB = int(os.getenv('REDIS_DB', 0))
    
    # Redis Cache Settings
    CACHE_DEFAULT_TIMEOUT = int(os.getenv('CACHE_DEFAULT_TIMEOUT', 30))
    STREAM_STATUS_CACHE_TIMEOUT = int(os.getenv('STREAM_STATUS_CACHE_TIMEOUT', 30))
    DASHBOARD_STATS_CACHE_TIMEOUT = int(os.getenv('DASHBOARD_STATS_CACHE_TIMEOUT', 30))
    SESSION_CACHE_TIMEOUT = int(os.getenv('SESSION_CACHE_TIMEOUT', 30))

    # ─── CORS ────────────────────────────────────────────────────────────
    CORS_SUPPORTS_CREDENTIALS = True
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', 'https://monitor.jetcamstudio.com,*').split(',')

    # ─── Monitoring Intervals ────────────────────────────────────────────
    STREAM_STATUS_CHECK_INTERVAL = int(os.getenv('STREAM_STATUS_CHECK_INTERVAL', '60'))
    VIEWER_COUNT_INTERVAL = int(os.getenv('VIEWER_COUNT_INTERVAL', '30'))
    NOTIFICATION_DEBOUNCE = int(os.getenv('NOTIFICATION_DEBOUNCE', '300'))
    AUDIO_SAMPLE_DURATION = float(os.getenv('AUDIO_SAMPLE_DURATION', '30'))
    AUDIO_BUFFER_SIZE = int(os.getenv('AUDIO_BUFFER_SIZE', '3'))
    AUDIO_SEGMENT_LENGTH = int(os.getenv('AUDIO_SEGMENT_LENGTH', '15'))
    AUDIO_ALERT_COOLDOWN = int(os.getenv('AUDIO_ALERT_COOLDOWN', '60'))
    VISUAL_ALERT_COOLDOWN = int(os.getenv('VISUAL_ALERT_COOLDOWN', '30'))
    CHAT_ALERT_COOLDOWN = int(os.getenv('CHAT_ALERT_COOLDOWN', '45'))
    NEGATIVE_SENTIMENT_THRESHOLD = float(os.getenv('NEGATIVE_SENTIMENT_THRESHOLD', '-0.5'))
    WHISPER_MODEL_SIZE = os.getenv('WHISPER_MODEL_SIZE', 'tiny')
    CONTINUOUS_MONITORING = os.getenv('CONTINUOUS_MONITORING', 'true').lower() == 'true'
    ENABLE_AUDIO_MONITORING = os.getenv('ENABLE_AUDIO_MONITORING', 'true').lower() == 'true'
    ENABLE_VIDEO_MONITORING = os.getenv('ENABLE_VIDEO_MONITORING', 'true').lower() == 'true'
    ENABLE_CHAT_MONITORING = os.getenv('ENABLE_CHAT_MONITORING', 'true').lower() == 'true'

def validate_env_vars():
    """Validate critical environment variables at startup."""
    required_vars = ['FLASK_SECRET_KEY', 'DATABASE_URL']
    for var in required_vars:
        if not os.getenv(var):
            logger.error(f"Missing required environment variable: {var}")
            raise ValueError(f"Missing required environment variable: {var}")
    try:
        float(os.getenv('AUDIO_SAMPLE_DURATION', '10'))
        logger.info("AUDIO_SAMPLE_DURATION is valid")
    except ValueError:
        logger.error("AUDIO_SAMPLE_DURATION must be a valid number")
        raise ValueError("AUDIO_SAMPLE_DURATION must be a valid number")

def configure_ssl_context():
    """Configure SSL context for the Flask application."""
    ssl_context = None
    enable_ssl = os.getenv('ENABLE_SSL', 'false').lower() == 'true'
    if enable_ssl:
        cert_dir = os.getenv('CERT_DIR', '/home/ec2-user/LiveStream_Monitoring_Vue3_Flask/backend')
        certfile = os.getenv('SSL_CERT_PATH', os.path.join(cert_dir, 'fullchain.pem'))
        keyfile = os.getenv('SSL_KEY_PATH', os.path.join(cert_dir, 'privkey.pem'))
        if os.path.exists(certfile) and os.path.exists(keyfile):
            ssl_context = (certfile, keyfile)
            logger.info(f"SSL Enabled with cert: {certfile} and key: {keyfile}")
        else:
            logger.error(f"SSL certificate files not found at {certfile} and {keyfile}")
            raise FileNotFoundError("SSL certificate files not found")
    return ssl_context

def create_app(config_class=Config, blueprint=None):
    """Create and configure Flask app."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = config_class.SQLALCHEMY_ENGINE_OPTIONS
    app.config['MONITOR_HOST'] = os.getenv('MONITOR_HOST', 'localhost')
    app.config['MONITOR_PORT'] = int(os.getenv('MONITOR_PORT', 5001))
    app.config['MONITOR_TIMEOUT'] = int(os.getenv('MONITOR_TIMEOUT', 30))
    app.config['MONITOR_RETRY_ATTEMPTS'] = int(os.getenv('MONITOR_RETRY_ATTEMPTS', 3))
    app.config['MONITOR_RETRY_DELAY'] = int(os.getenv('MONITOR_RETRY_DELAY', 5))
 
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError as e:
        logger.warning(f"Could not create instance path: {e}")

    # Validate environment variables
    validate_env_vars()

    # Initialize extensions
    db.init_app(app)
    socketio.init_app(app, cors_allowed_origins=config_class.CORS_ORIGINS, async_mode='gevent')
    
    if redis_service:
        redis_service.init_app(app)
        if redis_service.is_available():
            logger.info("Redis caching enabled")
            app.config['REDIS_ENABLED'] = True
        else:
            logger.warning("Redis unavailable - running without caching")
            app.config['REDIS_ENABLED'] = False
    else:
        app.config['REDIS_ENABLED'] = False
    
    CORS(
        app,
        supports_credentials=config_class.CORS_SUPPORTS_CREDENTIALS,
        origins=[u.strip() for u in config_class.CORS_ORIGINS],
        resources={r"/api/*": {}, r"/socket.io/*": {}}
    )

    @event.listens_for(Engine, "connect")
    def set_statement_timeout(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET statement_timeout = %s", [3000])
            cursor.close()
            dbapi_connection.commit()
        except Exception as e:
            logger.error(f"Failed to set statement_timeout: {e}")
            cursor.close()

    with app.app_context():
        try:
            NotificationService.init(app)
            NotificationService.start_scheduler(detection_only=(blueprint is not None))
            logger.info("Notification service initialized")
        except Exception as e:
            logger.error(f"Notification service init failed: {e}")
            raise

    if blueprint:
        app.register_blueprint(blueprint)
    else:
        from routes.auth_routes import auth_bp
        from routes.agent_routes import agent_bp
        from routes.stream_routes import stream_bp
        from routes.assignment_routes import assignment_bp
        from routes.dashboard_routes import dashboard_bp
        from routes.detection_routes import detection_bp
        from routes.health_routes import health_bp
        from routes.keyword_object_routes import keyword_bp
        from routes.messaging_routes import messaging_bp
        from routes.notification_routes import notification_bp
        from routes.cache_routes import cache_bp

        for bp in (
            auth_bp, agent_bp, stream_bp, assignment_bp, dashboard_bp,
            detection_bp, health_bp, keyword_bp, messaging_bp, notification_bp,
            cache_bp
        ):
            app.register_blueprint(bp)

    @app.errorhandler(404)
    def not_found(err):
        return {"error": "Not Found"}, 404

    @app.errorhandler(500)
    def server_error(err):
        app.logger.exception(err)
        return {"error": "Internal Server Error"}, 500

    return app