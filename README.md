# LiveStream Monitoring System - Backend

## Overview
This backend is a robust, scalable Flask-based system for real-time livestream monitoring, detection, and notification. It supports video, audio, and chat analysis, provides a RESTful API, and delivers real-time updates via WebSocket (Socket.IO). It is designed for both production and research environments, with modular services and strong security.

---

## Architecture
- **Flask**: Main web framework for REST API and Socket.IO endpoints
- **PostgreSQL**: Primary database (see `models.py`)
- **Socket.IO**: Real-time communication (see `extensions.py`, `socket_events.py`)
- **Celery/BackgroundScheduler**: For scheduled/background tasks
- **YOLO/Whisper**: ML models for video/audio analysis
- **Proxy/Redis**: Proxy rotation and caching
- **Gunicorn**: Production WSGI server (see `gunicorn.conf.py`)

### Main Modules
- `main.py`: Main entry point for the backend server
- `monitor_app.py`: Standalone monitoring app (can run separately)
- `models.py`: SQLAlchemy ORM models (User, Stream, DetectionLog, etc.)
- `routes/`: All API endpoints, organized by feature
- `services/`: Business logic and integrations (assignment, notification, etc.)
- `utils/`: Utility functions (notifications, proxy, etc.)
- `audio_processing.py`, `video_processing.py`, `chat_processing.py`: ML/AI logic

---

## Configuration
- All configuration is via environment variables (see `.env` and `config.py`).
- Example variables:
  - `FLASK_ENV`, `FLASK_DEBUG`, `FLASK_SECRET_KEY`
  - `DATABASE_URL` (PostgreSQL connection string)
  - `MONITOR_PORT`, `MONITOR_API_URL`, `MAIN_APP_HOST`, etc.
  - SSL: `SSL_CERT_PATH`, `SSL_KEY_PATH`
  - Admin: `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD`, `DEFAULT_ADMIN_EMAIL`
- To set up:
  1. Copy `.env.example` to `.env` and fill in your values.
  2. For production, ensure SSL certs are present and referenced in `.env`.

---

## Database
- Uses SQLAlchemy ORM (see `models.py`).
- To initialize:
  ```bash
  python db_init.py
  ```
- For a full reset (DANGER: deletes all data):
  ```bash
  python nuclear_drop_tables.py
  ```
- Default admin user is created if not present (see `main.py`).

---

## API Endpoints
All endpoints are prefixed with `/api/`.

### Authentication
- `POST /api/auth/login` — Login, returns JWT token
- `POST /api/auth/token-verify` — Verify/refresh token

### Streams & Monitoring
- `GET /api/streams` — List all streams
- `POST /api/monitoring/stream/<stream_id>/start` — Start monitoring a stream
- `POST /api/monitoring/stream/<stream_id>/stop` — Stop monitoring a stream
- `GET /api/monitor/detection-status/<stream_id>` — Get detection status
- `GET /api/monitor/health` — Health check
- `POST /api/monitoring/restart` — Restart all monitoring

### Notifications
- `GET /api/notifications` — List notifications
- `POST /api/notifications` — Create notification
- `POST /api/forward-socketio` — Forward Socket.IO event

### Messaging
- `GET /api/messages/<user_id>` — Get messages for a user
- `POST /api/messages/<user_id>` — Send message
- `DELETE /api/messages/<user_id>` — Delete conversation

### Agents & Assignments
- `GET /api/agents` — List agents
- `POST /api/agents` — Create agent
- `GET /api/assignments` — List assignments
- `POST /api/assignments` — Assign agent to stream

### More endpoints are available in `routes/` and documented in `API.md`.

---

## WebSocket Events
- Namespace: `/` (default), `/notifications`, `/messages`
- Events:
  - `notification`, `notification_update`
  - `stream_status`, `stream_update`
  - `message`, `typing`, `user_status`
- See `socket_events.py` and `routes/socketio_routes.py` for details.

---

## Services & Utilities
- **Notification Service**: Aggregates and emits notifications (see `services/notification_service.py`)
- **Assignment Service**: Manages agent-stream assignments
- **Proxy Handler**: Rotates proxies for scraping/streaming
- **Audio/Video/Chat Processing**: ML-based detection and analysis
- **Email/Telegram**: Sends alerts to users/agents

---

## Running & Deployment
### Development
```bash
pip install -r requirements.txt
python main.py
```

### Production
- Use Gunicorn with the provided config:
  ```bash
  gunicorn -c gunicorn.conf.py main:app
  ```
- Or run as a service (see `gunicorn.service`)
- For monitoring app:
  ```bash
  python monitor_app.py
  ```

---

## Example: Start Monitoring a Stream
```bash
curl -X POST https://<host>:<port>/api/monitoring/stream/123/start -H "Authorization: Bearer <token>"
```

---

## Security
- SSL/TLS enforced in production
- JWT token authentication
- Role-based access (admin/agent)
- CORS and CSRF protection
- Passwords hashed with Werkzeug

---

## Logs & Maintenance
- Logs: `db_init.log`, `nuclear_drop.log`, `pg_drop.log`
- Proxy list: `proxies.txt`
- Transcriptions: `transcriptions/`
- Model files: `yolo/`, `yolo*.pt`

---

## Troubleshooting
- If database is not ready, see logs and retry
- For SSL issues, check cert paths and permissions
- For proxy errors, update `proxies.txt`
- For model errors, ensure `.pt` files are present

---

## File/Folder Reference
- `routes/` — All API endpoints, grouped by feature
- `services/` — Business logic, integrations
- `utils/` — Helper functions (notifications, proxy, etc.)
- `transcriptions/` — Audio transcription outputs
- `yolo/` — YOLO model files
- `instance/` — SQLite DB for dev/testing

---

## License
Copyright © 2025 LiveStream Monitoring System