import multiprocessing
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SSL Configuration
enable_ssl = os.getenv('ENABLE_SSL', 'true').lower() == 'true'
certfile = None
keyfile = None

if enable_ssl:
    cert_dir = os.getenv('CERT_DIR', '/home/ec2-user/LiveStream_Monitoring_Vue3_Flask/backend')
    certfile = os.getenv('SSL_CERT_PATH', os.path.join(cert_dir, 'fullchain.pem'))
    keyfile = os.getenv('SSL_KEY_PATH', os.path.join(cert_dir, 'privkey.pem'))
    
    # Verify certificate files exist
    if not (os.path.exists(certfile) and os.path.exists(keyfile)):
        logger.error(f"SSL certificate files not found: cert={certfile}, key={keyfile}")
        raise FileNotFoundError("SSL certificate files not found")
    
    logger.info(f"SSL Enabled with cert: {certfile} and key: {keyfile}")

# Binding
bind = "0.0.0.0:5000"

# Worker Configuration
workers = 1  # e.g., 9 workers for 4 cores
worker_class = "geventwebsocket.gunicorn.workers.GeventWebSocketWorker"
timeout = 120  # Increased for long-running tasks
graceful_timeout = 60
keepalive = 5
# Connection settings
worker_connections = 1000
backlog = 2048

# Performance optimizations
max_requests = 0
max_requests_jitter = 0
preload_app = True
reuse_port = True

# Timeouts (in seconds)
timeout = 60  # Increased for long-running tasks
graceful_timeout = 30  # Increased for graceful shutdown
keepalive = 5  # Increased for WebSocket connections

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'
capture_output = True

# Security Headers
forwarded_allow_ips = "*"
proxy_protocol = True
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# Hooks
def worker_int(worker):
    worker.log.info("Worker received INT or QUIT signal")
    pid = os.getpid()
    worker.log.info("Worker syncing to disk before exit (pid: %s)", pid)

def post_fork(server, worker):
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def pre_fork(server, worker):
    pass

def pre_exec(server):
    server.log.info("Forked child, re-executing.")

def when_ready(server):
    print(f'Gunicorn server started with {server.cfg.workers} workers')

# Additional configuration for WebSocket support
raw_env = [
    'FLASK_ENV=production',
    'FLASK_DEBUG=False'
]