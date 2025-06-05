# main_extensions.py
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
socketio = SocketIO(async_mode='gevent')  # Use gevent for main app

# Configure executors
executors = {
    'default': ThreadPoolExecutor(1)
}

# Create scheduler
scheduler = BackgroundScheduler(
    executors=executors,
    job_defaults={
        'coalesce': False,
        'max_instances': 1,
        'misfire_grace_time': 30
    }
)

def init_extensions(app):
    try:
        logger.info("Initializing SQLAlchemy (main)")
        db.init_app(app)
        logger.info("Initializing Flask-Migrate (main)")
        migrate.init_app(app, db)
        logger.info("Initializing Flask-SocketIO (main)")
        socketio.init_app(app)
        logger.info("Flask-SocketIO initialized successfully (main)")
    except Exception as e:
        logger.error(f"Error initializing main extensions: {str(e)}")
        raise