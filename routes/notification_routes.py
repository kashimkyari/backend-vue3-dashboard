# routes/notification_routes.py
from flask import Blueprint, request, jsonify, session, current_app
from extensions import db, redis_service
from models import DetectionLog, User, Stream, Assignment
from utils import login_required
from utils.notifications import emit_notification, emit_notification_update
from sqlalchemy import or_
from datetime import datetime, timedelta
from sqlalchemy.orm import joinedload
from services.notification_service import NotificationService
import logging

notification_bp = Blueprint('notification', __name__)

# Agent cache for usernames
agent_cache = {}
all_agents_fetched = False

# Cache for recent status updates to prevent notification spam
status_update_cache = {}
DEBOUNCE_INTERVAL = 300  # 5 minutes in seconds

def fetch_all_agents():
    """Fetch all agents and cache their usernames"""
    global all_agents_fetched
    if all_agents_fetched:
        return
    try:
        agents = User.query.filter_by(role='agent').all()
        for agent in agents:
            agent_cache[agent.id] = agent.username or f"Agent {agent.id}"
        all_agents_fetched = True
    except Exception as e:
        logging.error(f"Error fetching all agents: {e}")

def fetch_agent_username(agent_id):
    """Fetch a single agent's username and cache it"""
    return NotificationService.fetch_agent_username(agent_id)

def get_stream_assignment(stream_url):
    """Get assignment info for a stream"""
    return NotificationService.get_stream_assignment(stream_url)

@notification_bp.route("/api/streams/<int:stream_id>/status", methods=["POST"])
def update_stream_status(stream_id):
    """Update stream status and emit notification if necessary"""
    try:
        data = request.get_json()
        new_status = data.get('status')
        if not new_status or new_status not in ['online', 'offline', 'monitoring']:
            return jsonify({"error": "Invalid or missing status"}), 400

        stream = Stream.query.get(stream_id)
        if not stream:
            return jsonify({"error": "Stream not found"}), 404

        # Check if status update is necessary
        current_time = datetime.utcnow()
        cache_key = f"stream_{stream_id}_status"
        last_update = status_update_cache.get(cache_key)

        if last_update and (current_time - last_update['timestamp']).total_seconds() < DEBOUNCE_INTERVAL and last_update['status'] == new_status:
            return jsonify({"message": "Status update debounced", "status": stream.status}), 200

        stream.status = new_status
        stream.is_monitored = new_status == 'monitoring'
        db.session.commit()

        # Update cache
        status_update_cache[cache_key] = {
            'timestamp': current_time,
            'status': new_status
        }

        # Emit notification for status change
        notification_data = {
            "id": stream.id,
            "event_type": "stream_status_update",
            "timestamp": current_time.isoformat(),
            "details": {
                "stream_id": stream.id,
                "status": new_status,
                "room_url": stream.room_url,
                "streamer_name": stream.streamer_username,
                "platform": stream.type,
                "assigned_agent": fetch_agent_username(stream.assignments[0].agent_id) if stream.assignments else "Unassigned"
            },
            "read": False,
            "room_url": stream.room_url,
            "streamer": stream.streamer_username,
            "platform": stream.type,
            "assigned_agent": fetch_agent_username(stream.assignments[0].agent_id) if stream.assignments else "Unassigned"
        }
        emit_notification(notification_data)

        # Notify assigned agent and admins
        if stream.assignments:
            agent = User.query.get(stream.assignments[0].agent_id)
            if agent and agent.receive_updates:
                NotificationService.send_user_notification(
                    agent, "stream_status_update", notification_data["details"],
                    stream.room_url, stream.type, stream.streamer_username
                )
        NotificationService.notify_admins(
            "stream_status_update", notification_data["details"],
            stream.room_url, stream.type, stream.streamer_username
        )

        return jsonify({"message": "Stream status updated", "status": new_status}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating stream status: {str(e)}")
        return jsonify({"error": str(e)}), 500

@notification_bp.route("/api/notifications", methods=["GET"])
def get_all_notifications():
    """Fetch notifications with pagination and optimized agent filtering"""
    try:
        user_id = session.get("user_id")
        user_role = session.get("user_role")
        
        # Pagination parameters
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        
        # Check Redis cache first
        cache_key = f"notifications:user:{user_id}:role:{user_role}:page:{page}:per_page:{per_page}"
        if current_app.config.get('REDIS_ENABLED') and redis_service.is_available():
            cached_data = redis_service.cache_get(cache_key)
            if cached_data:
                return jsonify(cached_data), 200

        # Original database query logic
        query = DetectionLog.query.options(joinedload(DetectionLog.assigned_user))
        
        if user_role == "agent":
            agent = User.query.get(user_id)
            if not agent:
                return jsonify({"error": "Agent not found"}), 404
                
            # Get assigned stream IDs
            assigned_stream_ids = [assignment.stream_id for assignment in agent.assignments]
            
            # Optimized query: filter by assigned_agent or assigned streams
            query = query.join(Assignment, DetectionLog.assignment_id == Assignment.id, isouter=True).filter(
                or_(
                    DetectionLog.assigned_agent == user_id,
                    Assignment.stream_id.in_(assigned_stream_ids)
                )
            )
        
        # Apply pagination and ordering
        notifications = query.order_by(DetectionLog.timestamp.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        ).items
        
        response_data = [{
            "id": n.id,
            "event_type": n.event_type,
            "timestamp": n.timestamp.isoformat(),
            "details": n.details,
            "read": n.read,
            "room_url": n.room_url,
            "streamer": n.details.get('streamer_name', 'Unknown'),
            "platform": n.details.get('platform', 'Unknown'),
            
        } for n in notifications]

        # Cache the response
        if current_app.config.get('REDIS_ENABLED') and redis_service.is_available():
            redis_service.cache_set(
                cache_key,
                response_data,
                expire=current_app.config.get('DASHBOARD_STATS_CACHE_TIMEOUT', 1800)
            )

        return jsonify(response_data), 200
    except Exception as e:
        logging.error(f"Error fetching notifications: {str(e)}")
        return jsonify({"error": str(e)}), 500

@notification_bp.route("/api/notifications", methods=["POST"])
def create_notification():
    """Create a new notification, avoiding duplicates based on event_type, room_url, and details"""
    try:
        data = request.get_json()
        
        if not data.get('event_type') or not data.get('room_url'):
            return jsonify({"error": "Missing required fields: event_type, room_url"}), 400
            
        # Check for existing notification to prevent duplicates
        current_time = datetime.utcnow()
        recent_time = current_time - timedelta(seconds=DEBOUNCE_INTERVAL)
        existing_notification = DetectionLog.query.filter(
            DetectionLog.event_type == data['event_type'],
            DetectionLog.room_url == data['room_url'],
            DetectionLog.details == data.get('details', {}),
            DetectionLog.timestamp >= recent_time
        ).first()
        
        if existing_notification:
            return jsonify({
                "message": "Notification already exists",
                "notification_id": existing_notification.id
            }), 200
            
        assignment_id, agent_id = get_stream_assignment(data.get('room_url'))
        
        notification = DetectionLog(
            event_type=data.get('event_type'),
            room_url=data.get('room_url'),
            timestamp=current_time,
            details=data.get('details', {}),
            read=data.get('read', False),
            assignment_id=assignment_id,
            detection_image=data.get('detection_image')
        )
        
        assigned_agent_username = fetch_agent_username(agent_id) if agent_id else 'Unassigned'
        notification.details['assigned_agent'] = assigned_agent_username
        
        db.session.add(notification)
        db.session.commit()
        
        notification_data = {
            "id": notification.id,
            "event_type": notification.event_type,
            "timestamp": notification.timestamp.isoformat(),
            "details": notification.details,
            "read": notification.read,
            "room_url": notification.room_url,
            "streamer": notification.details.get('streamer_name', 'Unknown'),
            "platform": notification.details.get('platform', 'Unknown'),
        }
        
        emit_notification(notification_data)
        
        if agent_id:
            agent = User.query.get(agent_id)
            if agent and agent.receive_updates:
                NotificationService.send_user_notification(
                    agent, notification.event_type, notification.details, 
                    notification.room_url, notification.details.get('platform'), 
                    notification.details.get('streamer_name'),
                    is_image=bool(notification.detection_image), 
                    image_data=notification.detection_image
                )
        NotificationService.notify_admins(
            notification.event_type, notification.details, 
            notification.room_url, notification.details.get('platform'), 
            notification.details.get('streamer_name')
        )
        
        return jsonify({
            "message": "Notification created successfully",
            "notification": notification_data
        }), 201
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error creating notification: {str(e)}")
        return jsonify({"error": str(e)}), 500

@notification_bp.route("/api/notifications/<int:notification_id>", methods=["GET"])
def get_notification(notification_id):
    """Fetch a single notification"""
    try:
        user_id = session.get("user_id")
        user_role = session.get("user_role")
        
        notification = DetectionLog.query.get(notification_id)
        if not notification:
            return jsonify({"error": "Notification not found"}), 404
            
        if user_role == "agent":
            agent = User.query.get(user_id)
            if not agent:
                return jsonify({"error": "Agent not found"}), 404
                
            if notification.assigned_agent and int(notification.assigned_agent) != user_id:
                stream = Stream.query.filter_by(room_url=notification.room_url).first()
                assigned_streams = [assignment.stream_id for assignment in agent.assignments]
                
                if not (stream and stream.id in assigned_streams):
                    return jsonify({"error": "Not authorized to view this notification"}), 403
        
        return jsonify({
            "id": notification.id,
            "event_type": notification.event_type,
            "timestamp": notification.timestamp.isoformat(),
            "details": notification.details,
            "read": notification.read,
            "room_url": notification.room_url,
            "streamer": notification.details.get('streamer_name', 'Unknown'),
            "platform": notification.details.get('platform', 'Unknown'),
            "assigned_agent": agent_cache.get(notification.assigned_agent, "Unassigned") if notification.assigned_agent else "Unassigned"
        }), 200
    except Exception as e:
        logging.error(f"Error fetching notification: {str(e)}")
        return jsonify({"error": str(e)}), 500

@notification_bp.route("/api/notifications/<int:notification_id>", methods=["PUT"])
def update_notification(notification_id):
    """Update an existing notification"""
    try:
        notification = DetectionLog.query.get(notification_id)
        if not notification:
            return jsonify({"error": "Notification not found"}), 404
            
        data = request.get_json()
        
        if 'event_type' in data:
            notification.event_type = data['event_type']
            
        if 'details' in data:
            notification.details = data['details']
            
        if 'room_url' in data:
            notification.room_url = data['room_url']
            assignment_id, agent_id = get_stream_assignment(data['room_url'])
            notification.assigned_agent = agent_id
            notification.assignment_id = assignment_id
            notification.details['assigned_agent'] = fetch_agent_username(agent_id) if agent_id else 'Unassigned'
            
        if 'read' in data:
            notification.read = data['read']
            
        db.session.commit()
        
        emit_notification_update(notification.id, 'updated')
        
        if notification.assigned_agent:
            agent = User.query.get(notification.assigned_agent)
            if agent and agent.receive_updates:
                NotificationService.send_user_notification(
                    agent, notification.event_type, notification.details, 
                    notification.room_url, notification.details.get('platform'), 
                    notification.details.get('streamer_name'),
                    is_image=bool(notification.detection_image), 
                    image_data=notification.detection_image
                )
        NotificationService.notify_admins(
            notification.event_type, notification.details, 
            notification.room_url, notification.details.get('platform'), 
            notification.details.get('streamer_name')
        )
        
        return jsonify({
            "message": "Notification updated successfully",
            "notification": {
                "id": notification.id,
                "event_type": notification.event_type,
                "timestamp": notification.timestamp.isoformat(),
                "details": notification.details,
                "read": notification.read,
                "room_url": notification.room_url,
                "assigned_agent": agent_cache.get(notification.assigned_agent, "Unassigned") if notification.assigned_agent else "Unassigned"
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating notification: {str(e)}")
        return jsonify({"error": str(e)}), 500

@notification_bp.route("/api/notifications/<int:notification_id>/read", methods=["PUT"])
def mark_notification_read(notification_id):
    """Mark a notification as read"""
    try:
        user_id = session.get("user_id")
        user_role = session.get("user_role")
        
        notification = DetectionLog.query.get(notification_id)
        if not notification:
            return jsonify({"message": "Notification not found"}), 404
            
        if user_role == "agent":
            agent = User.query.get(user_id)
            if not agent:
                return jsonify({"error": "Agent not found"}), 404
                
            if notification.assigned_agent and int(notification.assigned_agent) != user_id:
                stream = Stream.query.filter_by(room_url=notification.room_url).first()
                if not stream:
                    return jsonify({"error": "Notification not accessible"}), 403
                    
                assignment = Assignment.query.filter_by(agent_id=user_id, stream_id=stream.id).first()
                if not assignment:
                    return jsonify({"error": "Notification not accessible"}), 403
        
        notification.read = True
        db.session.commit()
        
        emit_notification_update(notification_id, 'read')
        
        return jsonify({"message": "Notification marked as read"}), 200
    except Exception as e:
        logging.error(f"Error marking notification as read: {str(e)}")
        return jsonify({"error": str(e)}), 500

@notification_bp.route("/api/notifications/read-all", methods=["PUT"])
def mark_all_notifications_read():
    """Mark all relevant notifications as read"""
    try:
        user_id = session.get("user_id")
        user_role = session.get("user_role")

        if user_role == "admin":
            notifications = DetectionLog.query.all()
            for notification in notifications:
                notification.read = True
                emit_notification_update(notification.id, 'read')
        else:
            agent = User.query.get(user_id)
            if not agent:
                return jsonify({"error": "Agent not found"}), 404

            assigned_streams = [assignment.stream_id for assignment in agent.assignments]
            notifications = DetectionLog.query.join(Assignment, DetectionLog.assignment_id == Assignment.id, isouter=True).filter(
                or_(
                    DetectionLog.assigned_agent == user_id,
                    Assignment.stream_id.in_(assigned_streams)
                )
            ).all()

            for notification in notifications:
                notification.read = True
                emit_notification_update(notification.id, 'read')

        db.session.commit()
        return jsonify({"message": "All notifications marked as read"}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error marking all notifications as read: {str(e)}")
        return jsonify({"error": str(e)}), 500

@notification_bp.route("/api/notifications/<int:notification_id>", methods=["DELETE"])
def delete_notification(notification_id):
    """Delete a notification"""
    try:
        notification = DetectionLog.query.get(notification_id)
        if not notification:
            return jsonify({"message": "Notification not found"}), 404
        db.session.delete(notification)
        db.session.commit()
        
        emit_notification_update(notification_id, 'deleted')
        
        return jsonify({"message": "Notification deleted"}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting notification: {str(e)}")
        return jsonify({"error": str(e)}), 500

@notification_bp.route("/api/notifications/delete-all", methods=["DELETE"])
def delete_all_notifications():
    """Delete all notifications"""
    try:
        notification_ids = [n.id for n in DetectionLog.query.all()]
        DetectionLog.query.delete()
        db.session.commit()
        
        for notification_id in notification_ids:
            emit_notification_update(notification_id, 'deleted')
        
        return jsonify({"message": "All notifications deleted"}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting all notifications: {str(e)}")
        return jsonify({"error": str(e)}), 500

@notification_bp.route("/api/notifications/forwarded", methods=["GET"])
def get_forwarded_notifications():
    """Fetch forwarded notifications"""
    try:
        forwarded = DetectionLog.query.filter(
            DetectionLog.assigned_agent.isnot(None)
        ).order_by(DetectionLog.timestamp.desc()).limit(100).all()
        
        return jsonify([{
            'id': n.id,
            'timestamp': n.timestamp.isoformat(),
            'assigned_agent': agent_cache.get(n.assigned_agent, "Unassigned") if n.assigned_agent else "Unassigned",
            'platform': n.details.get('platform'),
            'streamer': n.details.get('streamer_name'),
            'status': 'acknowledged' if n.read else 'pending'
        } for n in forwarded]), 200
    except Exception as e:
        logging.error(f"Error fetching forwarded notifications: {str(e)}")
        return jsonify({"error": str(e)}), 500

@notification_bp.route("/api/notifications/<int:notification_id>/forward", methods=["POST"])
def forward_notification(notification_id):
    """Forward a notification to an agent"""
    try:
        data = request.get_json()
        agent_id = data.get("agent_id")

        notification = DetectionLog.query.get(notification_id)
        agent = User.query.filter_by(id=agent_id, role="agent").first()

        if not notification or not agent:
            return jsonify({"message": "Invalid notification or agent"}), 404

        details = notification.details or {}
        details['assigned_agent'] = NotificationService.fetch_agent_username(agent_id)
        notification.details = details
        notification.assigned_agent = agent.id
        assignment_id, _ = get_stream_assignment(notification.room_url)
        notification.assignment_id = assignment_id

        db.session.commit()

        emit_notification_update(notification_id, 'forwarded')

        message_details = {
            "event_type": notification.event_type,
            "timestamp": notification.timestamp.isoformat(),
            "stream_url": notification.room_url,
            "streamer": notification.details.get('streamer_name', 'Unknown'),
            "platform": notification.details.get('platform', 'Unknown')
        }

        if notification.event_type == 'object_detection':
            message_details.update({
                "detections": notification.details.get('detections', []),
                "annotated_image": bool(notification.detection_image)
            })
        elif notification.event_type == 'chat_detection':
            message_details.update({
                "keywords": notification.details.get('keywords', []),
                "messages": notification.details.get('detections', [])
            })
        elif notification.event_type == 'audio_detection':
            message_details.update({
                "keyword": notification.details.get('keyword'),
                "transcript": notification.details.get('transcript')
            })

        sys_msg = ChatMessage(
            sender_id=session['user_id'],
            receiver_id=agent.id,
            message=f"Ã°ï¿½ï¿½Â¨ Forwarded {notification.event_type.replace('_', ' ').title()} Alert",
            details=message_details,
            is_system=True,
            timestamp=datetime.utcnow()
        )

        db.session.add(sys_msg)
        db.session.commit()

        emit_message_update({
            "id": sys_msg.id,
            "sender_id": sys_msg.sender_id,
            "receiver_id": sys_msg.receiver_id,
            "message": sys_msg.message,
            "timestamp": sys_msg.timestamp.isoformat(),
            "is_system": True,
            "read": False,
            "details": message_details
        })

        if agent.receive_updates:
            NotificationService.send_user_notification(
                agent, notification.event_type, notification.details, 
                notification.room_url, notification.details.get('platform'), 
                notification.details.get('streamer_name'),
                is_image=bool(notification.detection_image), 
                image_data=notification.detection_image
            )

        return jsonify({
            "message": "Notification forwarded to agent",
            "agent_id": agent.id,
            "agent_username": agent_cache.get(agent_id, "Unassigned")
        }), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error forwarding notification: {str(e)}")
        return jsonify({"error": str(e)}), 500