# gunicorn_monitor.conf.py
import os
import multiprocessing

# Server socket
bind = f"0.0.0.0:{os.getenv('MONITOR_PORT', 5001)}"
backlog = 2048

# Worker processes
workers = max(1, multiprocessing.cpu_count() // 2)  # Use half the CPU cores
threads = max(4, multiprocessing.cpu_count() * 2)  # More threads for I/O-bound tasks
worker_class = "gevent"  # Use standard gevent worker instead of geventwebsocket
worker_connections = 1000
timeout = 120
keepalive = 60

# Restart workers
max_requests = 1000
max_requests_jitter = 100
preload_app = True

# SSL Configuration
certfile = os.getenv('SSL_CERT_PATH', '/home/ec2-user/LiveStream_Monitoring_Vue3_Flask/backend/fullchain.pem')
keyfile = os.getenv('SSL_KEY_PATH', '/home/ec2-user/LiveStream_Monitoring_Vue3_Flask/backend/privkey.pem')

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = 'monitor-app'

# Server mechanics
daemon = False
pidfile = '/tmp/monitor-app.pid'
user = None
group = None
tmp_upload_dir = None

# SSL Security
ssl_version = 2  # TLS
ciphers = 'ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS'

def when_ready(server):
    server.log.info("Monitor app server is ready. Spawning workers")

def worker_int(worker):
    worker.log.info("Worker received INT or QUIT signal")

def pre_fork(server, worker):
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def post_fork(server, worker):
    server.log.info("Worker spawned (pid v%s)", worker.pid)
    from monitor_app import app  # Import app to ensure context
    with app.app_context():  # Initialize app context in each worker
        pass

def post_worker_init(worker):
    worker.log.info("Worker initialized (pid: %s)", worker.pid)

def worker_abort(worker):
    worker.log.info("Worker aborted (pid: %s)", worker.pid)