from datetime import datetime, timedelta
import re
from flask import Blueprint, request, jsonify, session, current_app
from extensions import db
from models import Stream, ChaturbateStream, StripchatStream, DetectionLog, User, Assignment
from utils import login_required
from scraping import scrape_chaturbate_data, scrape_stripchat_data, stream_creation_jobs, run_stream_creation_job, refresh_chaturbate_stream, refresh_stripchat_stream
import uuid
from monitoring import refresh_and_monitor_streams
import threading
from sqlalchemy.orm import joinedload
import logging
from utils.notifications import emit_stream_update
from services.assignment_service import AssignmentService
from services.notification_service import NotificationService

stream_bp = Blueprint('stream', __name__)

from threading import Lock
job_lock = Lock()

def cleanup_jobs():
    """Clean up completed or expired jobs."""
    with job_lock:
        expired_jobs = []
        current_time = datetime.now()
        
        for job_id, job in stream_creation_jobs.items():
            try:
                # Check if created_at exists and is valid
                if "created_at" not in job:
                    logging.warning(f"Job {job_id} missing 'created_at' field, marking for cleanup")
                    expired_jobs.append(job_id)
                    continue
                
                # Parse the created_at timestamp
                try:
                    created_at = datetime.fromisoformat(job["created_at"])
                except (ValueError, TypeError) as e:
                    logging.warning(f"Job {job_id} has invalid 'created_at' format: {job.get('created_at')}, marking for cleanup. Error: {e}")
                    expired_jobs.append(job_id)
                    continue
                
                # Calculate time differences
                time_since_creation = current_time - created_at
                
                # Check cleanup conditions
                job_progress = job.get("progress", 0)
                
                # Clean up if:
                # 1. Job is completed (100%) and older than 5 minutes, OR
                # 2. Job is older than 1 hour regardless of progress
                if ((job_progress >= 100 and time_since_creation > timedelta(minutes=5)) or 
                    (time_since_creation > timedelta(hours=1))):
                    expired_jobs.append(job_id)
                    
            except Exception as e:
                logging.error(f"Error processing job {job_id} during cleanup: {e}")
                # Mark problematic jobs for cleanup to prevent repeated errors
                expired_jobs.append(job_id)
        
        # Remove expired jobs
        for job_id in expired_jobs:
            try:
                del stream_creation_jobs[job_id]
                logging.debug(f"Cleaned up job {job_id}")
            except KeyError:
                logging.warning(f"Job {job_id} already removed during cleanup")
        
        logging.info(f"Cleaned up {len(expired_jobs)} jobs. {len(stream_creation_jobs)} jobs remaining.")
# --------------------------------------------------------------------
# Stream Management Endpoints
# --------------------------------------------------------------------
@stream_bp.route("/api/streams", methods=["GET"])
def get_streams():
    platform = request.args.get("platform", "").strip().lower()
    streamer = request.args.get("streamer", "").strip().lower()
    query = Stream.query.options(
        joinedload(Stream.assignments).joinedload(Assignment.agent)
    )
    if platform == "chaturbate":
        query = ChaturbateStream.query.options(
            joinedload(ChaturbateStream.assignments).joinedload(Assignment.agent)
        ).filter(ChaturbateStream.streamer_username.ilike(f"%{streamer}%"))
    elif platform == "stripchat":
        query = StripchatStream.query.options(
            joinedload(StripchatStream.assignments).joinedload(Assignment.agent)
        ).filter(StripchatStream.streamer_username.ilike(f"%{streamer}%"))
    if streamer and not platform:
        query = query.filter(Stream.streamer_username.ilike(f"%{streamer}%"))
    streams = query.all()
    
    streams_data = []
    for stream in streams:
        assignments_data = []
        for assignment in stream.assignments:
            agent_data = None
            if assignment and assignment.agent:
                agent = User.query.filter_by(id=assignment.agent_id, role="agent").first()
                if agent:
                    agent_data = {
                        "id": agent.id,
                        "username": agent.username,
                        "role": agent.role,
                        "online": agent.online
                    }
                else:
                    current_app.logger.warning(f"Agent with ID {assignment.agent_id} not found for stream {stream.id}")
                    agent_data = None
            assignments_data.append({
                **assignment.serialize(),
                "agent": agent_data
            })
        stream_data = {
            **stream.serialize(),
            "assignments": assignments_data
        }
        streams_data.append(stream_data)
    
    return jsonify(streams_data)

@stream_bp.route("/api/streams", methods=["POST"])

def create_stream():
    data = request.get_json()
    platform = data.get("platform", "Chaturbate").strip().lower()
    room_url = data.get("room_url", "").strip().lower()
    agent_id = data.get("agent_id")
    notes = data.get("notes")
    priority = data.get("priority", "normal")

    if not room_url:
        return jsonify({"message": "Room URL required"}), 400
    if platform not in ["chaturbate", "stripchat"]:
        return jsonify({"message": "Invalid platform"}), 400
    if platform == "chaturbate" and "chaturbate.com/" not in room_url:
        return jsonify({"message": "Invalid Chaturbate URL"}), 400
    if platform == "stripchat" and "stripchat.com/" not in room_url:
        return jsonify({"message": "Invalid Stripchat URL"}), 400
    if Stream.query.filter_by(room_url=room_url).first():
        return jsonify({"message": "Stream exists"}), 400

    try:
        streamer_username = room_url.rstrip("/").split("/")[-1]
        scraped_data = None
        if platform == "chaturbate":
            scraped_data = scrape_chaturbate_data(room_url)
            if not scraped_data or 'chaturbate_m3u8_url' not in scraped_data:
                return jsonify({"message": "Failed to scrape Chaturbate details"}), 500
            stream = ChaturbateStream(
                room_url=room_url,
                streamer_username=streamer_username,
                type="chaturbate",
                chaturbate_m3u8_url=scraped_data["chaturbate_m3u8_url"],
            )
        else:
            scraped_data = scrape_stripchat_data(room_url)
            if not scraped_data or 'stripchat_m3u8_url' not in scraped_data:
                return jsonify({"message": "Failed to scrape Stripchat details"}), 500
            stream = StripchatStream(
                room_url=room_url,
                streamer_username=streamer_username,
                type="stripchat",
                stripchat_m3u8_url=scraped_data["stripchat_m3u8_url"],
            )

        db.session.add(stream)
        db.session.flush()  # Get stream ID before commit

        # Handle assignment
        assignment = None
        created = False
        if agent_id:
            assignment, created = AssignmentService.assign_stream_to_agent(
                stream_id=stream.id,
                agent_id=agent_id,
                assigner_id=session.get("user_id"),
                notes=notes,
                priority=priority,
                metadata={"source": "manual_creation"},
            )
        else:
            assignment, created = AssignmentService.auto_assign_stream(
                stream_id=stream.id,
                assigner_id=session.get("user_id"),
            )

        db.session.commit()

        # Notify admins about stream creation
        NotificationService.notify_admins(
            'stream_created',
            {
                'message': f"New stream created: {streamer_username}",
                'room_url': room_url,
                'streamer_username': streamer_username,
                'platform': platform,
                'assignment_id': assignment.id if assignment else None,
            },
            room_url,
            platform,
            streamer_username,
        )

        # Emit stream update
        stream_data = {
            "id": stream.id,
            "type": stream.type,
            "room_url": stream.room_url,
            "streamer_username": stream.streamer_username,
            "status": stream.status,
            "action": "created",
            "assignment": assignment.serialize() if assignment else None,
        }
        emit_stream_update(stream_data)

        return jsonify({
            "message": "Stream created",
            "stream": stream.serialize(),
            "assignment": assignment.serialize() if assignment else None,
        }), 201
    except Exception as e:
        db.session.rollback()
        logging.error(f"Stream creation failed: {str(e)}")
        return jsonify({"message": "Stream creation failed", "error": str(e)}), 500

@stream_bp.route("/api/streams/<int:stream_id>", methods=["PUT"])

def update_stream(stream_id):
    stream = Stream.query.get(stream_id)
    if not stream:
        return jsonify({"message": "Stream not found"}), 404

    data = request.get_json()
    agent_ids = data.get("agent_ids", [])
    notes = data.get("notes")
    priority = data.get("priority", "normal")
    refresh = data.get("refresh", False)

    try:
        # Update stream fields
        if "room_url" in data and data["room_url"].strip():
            existing = Stream.query.filter(
                Stream.room_url == data["room_url"].strip(), Stream.id != stream_id
            ).first()
            if existing:
                return jsonify({"message": "Room URL already exists for another stream"}), 400
            stream.room_url = data["room_url"].strip()

        # Refresh stream data if requested
        if refresh and len(data.keys()) > 1:  # Don't refresh if only updating assignments
            if stream.type == "chaturbate":
                child_stream = ChaturbateStream.query.get(stream_id)
                scraped_data = scrape_chaturbate_data(stream.room_url)
                if scraped_data and 'chaturbate_m3u8_url' in scraped_data:
                    child_stream.chaturbate_m3u8_url = scraped_data["chaturbate_m3u8_url"]
            elif stream.type == "stripchat":
                child_stream = StripchatStream.query.get(stream_id)
                scraped_data = scrape_stripchat_data(stream.room_url)
                if scraped_data and 'stripchat_m3u8_url' in scraped_data:
                    child_stream.stripchat_m3u8_url = scraped_data["stripchat_m3u8_url"]

        # Handle assignments
        if agent_ids:
            # Clear existing assignments
            Assignment.query.filter_by(stream_id=stream_id, status='active').delete()
            assignments = []
            for agent_id in agent_ids:
                assignment, created = AssignmentService.assign_stream_to_agent(
                    stream_id=stream_id,
                    agent_id=agent_id,
                    assigner_id=session.get("user_id"),
                    notes=notes,
                    priority=priority,
                    metadata={"source": "manual_update"},
                )
                assignments.append(assignment)

        db.session.commit()

        # Emit stream update
        stream_data = {
            "id": stream.id,
            "type": stream.type,
            "room_url": stream.room_url,
            "streamer_username": stream.streamer_username,
            "status": stream.status,
            "action": "updated",
            "assignments": [a.serialize() for a in stream.assignments],
        }
        emit_stream_update(stream_data)

        return jsonify({
            "message": "Stream updated",
            "stream": stream.serialize(),
            "assignments": [a.serialize() for a in stream.assignments],
        }), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Stream update failed: {str(e)}")
        return jsonify({"message": "Stream update failed", "error": str(e)}), 500

@stream_bp.route("/api/streams/<int:stream_id>", methods=["DELETE"])

def delete_stream(stream_id):
    stream = Stream.query.get(stream_id)
    if not stream:
        return jsonify({"message": "Stream not found"}), 404

    try:
        # Notify admins and assigned agents
        for assignment in stream.assignments:
            NotificationService.send_user_notification(
                assignment.agent,
                'stream_deleted',
                {
                    'message': f"Stream {stream.streamer_username} has been deleted",
                    'room_url': stream.room_url,
                    'streamer_username': stream.streamer_username,
                    'platform': stream.type,
                },
                stream.room_url,
                stream.type,
                stream.streamer_username,
            )
        NotificationService.notify_admins(
            'stream_deleted',
            {
                'message': f"Stream {stream.streamer_username} deleted",
                'room_url': stream.room_url,
                'streamer_username': stream.streamer_username,
                'platform': stream.type,
            },
            stream.room_url,
            stream.type,
            stream.streamer_username,
        )

        db.session.delete(stream)
        db.session.commit()

        # Emit stream update
        emit_stream_update({
            "id": stream_id,
            "type": stream.type,
            "room_url": stream.room_url,
            "streamer_username": stream.streamer_username,
            "action": "deleted",
        })

        return jsonify({"message": "Stream deleted"}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Stream deletion failed: {str(e)}")
        return jsonify({"message": "Stream deletion failed", "error": str(e)}), 500

@stream_bp.route("/api/streams/refresh/chaturbate", methods=["POST"])

def refresh_chaturbate_route():
    data = request.get_json()
    room_slug = data.get("room_slug", "").strip()
    if not room_slug:
        return jsonify({"message": "Room slug is required"}), 400

    from scraping import refresh_chaturbate_stream
    new_url = refresh_chaturbate_stream(room_slug)
    if new_url:
        NotificationService.notify_admins(
            'stream_refreshed',
            {
                'message': f"Chaturbate stream {room_slug} refreshed",
                'room_url': f"https://chaturbate.com/{room_slug}/",
                'streamer_username': room_slug,
                'platform': 'chaturbate',
                'new_url': new_url,
            },
            f"https://chaturbate.com/{room_slug}/",
            'chaturbate',
            room_slug,
        )
        return jsonify({
            "message": "Stream refreshed successfully",
            "m3u8_url": new_url,
        }), 200
    return jsonify({"message": "Failed to refresh stream"}), 500

@stream_bp.route("/api/streams/refresh/stripchat", methods=["POST"])

def refresh_stripchat_route():
    data = request.get_json()
    room_url = data.get("room_url", "").strip()
    if not room_url:
        return jsonify({"message": "Room URL is required"}), 400

    from scraping import refresh_stripchat_stream
    new_url = refresh_stripchat_stream(room_url)
    if new_url:
        streamer_username = room_url.rstrip("/").split("/")[-1]
        NotificationService.notify_admins(
            'stream_refreshed',
            {
                'message': f"Stripchat stream {streamer_username} refreshed",
                'room_url': room_url,
                'streamer_username': streamer_username,
                'platform': 'stripchat',
                'new_url': new_url,
            },
            room_url,
            'stripchat',
            streamer_username,
        )
        return jsonify({
            "message": "Stream refreshed successfully",
            "m3u8_url": new_url,
        }), 200
    return jsonify({"message": "Failed to refresh stream"}), 500

@stream_bp.route('/api/streams/<int:stream_id>/status', methods=['POST'])

def update_stream_status(stream_id):
    stream = Stream.query.get(stream_id)
    if not stream:
        return jsonify({'message': 'Stream not found'}), 404

    data = request.get_json()
    status = data.get('status')
    if status not in ['online', 'offline']:
        return jsonify({'message': 'Invalid status. Use online or offline'}), 400

    try:
        stream.status = status
        db.session.commit()

        # Notify admins and assigned agents
        NotificationService.notify_admins(
            'stream_status_updated',
            {
                'message': f"Stream {stream.streamer_username} status updated to {status}",
                'room_url': stream.room_url,
                'streamer_username': stream.streamer_username,
                'platform': stream.type,
                'status': status,
            },
            stream.room_url,
            stream.type,
            stream.streamer_username,
        )
        for assignment in stream.assignments:
            NotificationService.send_user_notification(
                assignment.agent,
                'stream_status_updated',
                {
                    'message': f"Stream {stream.streamer_username} status updated to {status}",
                    'room_url': stream.room_url,
                    'streamer_username': stream.streamer_username,
                    'platform': stream.type,
                    'status': status,
                },
                stream.room_url,
                stream.type,
                stream.streamer_username,
            )

        # Emit update
        stream_data = {
            'id': stream.id,
            'type': stream.type,
            'room_url': stream.room_url,
            'streamer_username': stream.streamer_username,
            'status': stream.status,
            'action': 'status_update',
        }
        emit_stream_update(stream_data)

        return jsonify({
            'message': f'Stream status updated to {status}',
            'stream': stream.serialize(),
        }), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Stream status update failed: {str(e)}")
        return jsonify({"message": "Stream status update failed", "error": str(e)}), 500

@stream_bp.route("/api/streams/interactive", methods=["POST"])
def interactive_create_stream():
    try:
        if not request.is_json:
            return jsonify({
                "message": "Request must be JSON",
                "error": "invalid_content_type",
            }), 400

        data = request.get_json()
        required_fields = ["room_url", "platform"]
        missing = [field for field in required_fields if field not in data]
        if missing:
            return jsonify({
                "message": f"Missing required fields: {', '.join(missing)}",
                "error": "missing_fields",
                "missing": missing,
            }), 400

        room_url = data.get("room_url", "").strip().lower()
        platform = data.get("platform", "").strip().lower()
        agent_id = data.get("agent_id")
        notes = data.get("notes")
        priority = data.get("priority", "normal")

        if not room_url:
            return jsonify({
                "message": "Room URL cannot be empty",
                "error": "invalid_url",
            }), 400

        platform_validations = {
            "chaturbate": {
                "domain": "chaturbate.com",
                "model": ChaturbateStream,
                "url_pattern": r"https?://(www\.)?chaturbate\.com/[a-zA-Z0-9_]+/?$",
            },
            "stripchat": {
                "domain": "stripchat.com",
                "model": StripchatStream,
                "url_pattern": r"https?://(www\.)?stripchat\.com/[a-zA-Z0-9_]+/?$",
            },
        }

        if platform not in platform_validations:
            return jsonify({
                "message": f"Invalid platform. Valid options: {', '.join(platform_validations.keys())}",
                "error": "invalid_platform",
            }), 400

        if not re.match(platform_validations[platform]["url_pattern"], room_url):
            return jsonify({
                "message": f"Invalid {platform} URL format",
                "error": "invalid_url_format",
                "example": f"https://{platform_validations[platform]['domain']}/username",
            }), 400

        if agent_id:
            agent = User.query.filter_by(id=agent_id, role="agent").first()
            if not agent:
                return jsonify({
                    "message": "Specified agent does not exist",
                    "error": "invalid_agent_id",
                }), 400

        existing_stream = Stream.query.filter_by(room_url=room_url).first()
        if existing_stream:
            return jsonify({
                "message": "Stream already exists",
                "error": "duplicate_stream",
                "existing_id": existing_stream.id,
            }), 409

        with job_lock:
            job_id = str(uuid.uuid4())
            stream_creation_jobs[job_id] = {
                "progress": 0,
                "message": "Initializing",
                "created_at": datetime.now().isoformat(),
                "room_url": room_url,
                "platform": platform,
                "agent_id": agent_id,
                "notes": notes,
                "priority": priority,
                "error": None,
                "stream": None,
                "assignment": None,
                "stream_id": None,  # Store stream ID once created
            }

            def run_job_in_thread(app, job_id, room_url, platform, agent_id, notes, priority):
                with app.app_context():
                    try:
                        run_stream_creation_job(app, job_id, room_url, platform, agent_id, notes, priority)
                    except Exception as e:
                        with job_lock:
                            stream_creation_jobs[job_id]["error"] = str(e)
                        logging.error(f"Stream creation job {job_id} failed: {str(e)}")
                    finally:
                        cleanup_jobs()

            thread = threading.Thread(
                target=run_job_in_thread,
                args=(current_app._get_current_object(), job_id, room_url, platform, agent_id, notes, priority)
            )
            thread.start()

            return jsonify({
                "message": "Stream creation started",
                "job_id": job_id,
                "monitor_url": f"/api/streams/interactive/sse?job_id={job_id}",
            }), 202

    except Exception as e:
        logging.error(f"Interactive stream creation failed: {str(e)}")
        return jsonify({
            "message": "Internal server error",
            "error": "server_error",
            "details": str(e),
        }), 500

@stream_bp.route("/api/streams/interactive/sse")
def stream_creation_sse():
    job_id = request.args.get("job_id")
    if not job_id:
        return jsonify({"message": "Job id required"}), 400

    import json
    import time
    from flask import Response

    def event_stream():
        try:
            last_progress = -1
            while True:
                with job_lock:
                    job_status = stream_creation_jobs.get(job_id)
                    if not job_status:
                        # Check if stream exists in database
                        stream = Stream.query.filter_by(room_url=request.args.get("room_url", "")).first()
                        if stream:
                            yield f"event: completed\ndata: {json.dumps({'message': 'Stream created', 'stream_id': stream.id})}\n\n"
                        else:
                            yield f"event: error\ndata: {json.dumps({'message': 'Job not found'})}\n\n"
                        break

                    if job_status["progress"] != last_progress or job_status.get("error"):
                        data = json.dumps({
                            "progress": job_status["progress"],
                            "message": job_status["message"],
                            "error": job_status.get("error"),
                            "estimated_time": job_status.get("estimated_time"),
                            "stream": job_status.get("stream"),
                            "assignment": job_status.get("assignment"),
                            "stream_id": job_status.get("stream_id"),
                        })
                        yield f"data: {data}\n\n"
                        last_progress = job_status["progress"]

                    if job_status["progress"] >= 100 or job_status.get("error"):
                        if job_status.get("stream"):
                            yield f"event: completed\ndata: {json.dumps({'stream': job_status['stream'], 'stream_id': job_status['stream_id']})}\n\n"
                        break
                time.sleep(0.5)
        except GeneratorExit:
            pass

    return Response(event_stream(), mimetype="text/event-stream")

@stream_bp.route("/api/streams/interactive/status", methods=["GET"])
def stream_creation_status():
    job_id = request.args.get("job_id")
    if not job_id:
        return jsonify({"message": "Job ID required"}), 400

    with job_lock:
        job_status = stream_creation_jobs.get(job_id)
        if not job_status:
            # Check if stream exists in database
            stream = Stream.query.filter_by(room_url=request.args.get("room_url", "")).first()
            if stream:
                return jsonify({
                    "progress": 100,
                    "message": "Stream created",
                    "stream_id": stream.id,
                    "stream": stream.serialize(),
                    "assignment": stream.assignments[0].serialize() if stream.assignments else None,
                })
            return jsonify({"message": "Job not found", "error": "Job not found"}), 404

        return jsonify({
            "progress": job_status["progress"],
            "message": job_status["message"],
            "error": job_status.get("error"),
            "estimated_time": job_status.get("estimated_time"),
            "stream": job_status.get("stream"),
            "assignment": job_status.get("assignment"),
            "stream_id": job_status.get("stream_id"),
        })

@stream_bp.route("/api/streams/interactive/cleanup", methods=["POST"])

def cleanup_jobs_route():
    cleanup_jobs()
    return jsonify({
        "message": f"Cleaned up old jobs",
        "remaining_jobs": len(stream_creation_jobs),
    })

@stream_bp.route('/api/streams/refresh_selected', methods=['POST'])

def refresh_selected_streams():
    data = request.get_json()
    stream_ids = data.get('stream_ids', [])
    if not stream_ids:
        return jsonify({'error': 'No stream IDs provided'}), 400
    try:
        success = refresh_and_monitor_streams(stream_ids)
        if success:
            return jsonify({'message': 'Streams refreshed and monitoring started'})
        return jsonify({'error': 'Failed to refresh some or all streams'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500