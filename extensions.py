# extensions.py
import logging
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_socketio import SocketIO
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = SQLAlchemy()
migrate = Migrate()
socketio = SocketIO(async_mode='gevent')  # Use gevent for async

# Configure executors with larger pool size and proper shutdown
executors = {
    'default': ThreadPoolExecutor(1)  # Increase thread pool size
}

# Create scheduler with better configuration
scheduler = BackgroundScheduler(
    executors=executors,
    job_defaults={
        'coalesce': False,
        'max_instances': 1,
        'misfire_grace_time': 30
    }
)

# Import Redis service
try:
    from redis_service import redis_service
    logger.info("Redis service imported successfully")
except ImportError as e:
    logger.warning(f"Redis service not available: {e}")
    redis_service = None

def init_extensions(app):
    try:
        logger.info("Initializing SQLAlchemy")
        db.init_app(app)
        logger.info("Initializing Flask-Migrate")
        migrate.init_app(app, db)
        logger.info("Initializing Flask-SocketIO")
        socketio.init_app(app)
        logger.info("Flask-SocketIO initialized successfully")
        
        # Initialize Redis if available
        if redis_service:
            logger.info("Initializing Redis service")
            redis_service.init_app(app)
            if redis_service.is_available():
                logger.info("Redis service initialized successfully")
            else:
                logger.warning("Redis service unavailable - continuing without caching")
        
    except Exception as e:
        logger.error(f"Error initializing extensions: {str(e)}")
        raise