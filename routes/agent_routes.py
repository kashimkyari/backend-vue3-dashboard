# routes/agent_routes.py
from flask import Blueprint, request, jsonify, session
from extensions import db
from models import User, Assignment, PasswordReset, PasswordResetToken, DetectionLog, MessageAttachment, ChatMessage
from utils import login_required

agent_bp = Blueprint('agent', __name__)

# --------------------------------------------------------------------
# Agent Management Endpoints
# --------------------------------------------------------------------
@agent_bp.route("/api/agents", methods=["GET"])
@login_required(role=["admin", "agent"])
def get_agents():
    agents = User.query.filter_by(role="agent").all()
    return jsonify([agent.serialize() for agent in agents])

@agent_bp.route("/api/agents", methods=["POST"])

def create_agent():
    data = request.get_json()
    required_fields = ["username", "password"]
    if any(field not in data for field in required_fields):
        return jsonify({"message": "Missing required fields"}), 400
    if User.query.filter_by(username=data["username"]).first():
        return jsonify({"message": "Username already exists"}), 400
    
    agent = User(
        username=data["username"],
        password=data["password"],
        role="agent",
        email=data.get("email", ""),
        receive_updates=data.get("receive_updates", False)
    )
    db.session.add(agent)
    db.session.commit()
    return jsonify({"message": "Agent created", "agent": agent.serialize()}), 201

@agent_bp.route("/api/agents/<int:agent_id>", methods=["PUT"])

def update_agent(agent_id):
    agent = User.query.filter_by(id=agent_id, role="agent").first()
    if not agent:
        return jsonify({"message": "Agent not found"}), 404
    data = request.get_json()
    
    if "username" in data and (new_uname := data["username"].strip()):
        if User.query.filter(User.username == new_uname, User.id != agent_id).first():
            return jsonify({"message": "Username already taken"}), 400
        agent.username = new_uname
    
    if "password" in data and (new_pwd := data["password"].strip()):
        agent.password = new_pwd
    
    if "online" in data:
        agent.online = bool(data["online"])
    
    if "email" in data:
        agent.email = data["email"]
    
    if "receive_updates" in data:
        agent.receive_updates = bool(data["receive_updates"])
    
    db.session.commit()
    return jsonify({"message": "Agent updated", "agent": agent.serialize()})

@agent_bp.route("/api/agents/<int:agent_id>", methods=["DELETE"])

def delete_agent(agent_id):
    agent = User.query.filter_by(id=agent_id, role="agent").first()
    if not agent:
        return jsonify({"message": "Agent not found"}), 404
    
    try:
        # Unassign detection logs (set assigned_agent to null)
        DetectionLog.query.filter_by(assigned_agent=agent_id).update({"assigned_agent": None})
        
        # Delete related message attachments
        MessageAttachment.query.filter_by(user_id=agent_id).delete()
        
        # Delete related chat messages (where agent is sender or receiver)
        ChatMessage.query.filter((ChatMessage.sender_id == agent_id) | (ChatMessage.receiver_id == agent_id)).delete()
        
        # Delete related password resets
        PasswordReset.query.filter_by(user_id=agent_id).delete()
        
        # Delete related password reset tokens
        PasswordResetToken.query.filter_by(user_id=agent_id).delete()
        
        # Delete the agent (assignments are cascaded automatically due to cascade="all, delete")
        db.session.delete(agent)
        db.session.commit()
        
        return jsonify({"message": "Agent deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Failed to delete agent: {str(e)}"}), 500

# Add a new endpoint to get streams assigned to an agent
@agent_bp.route("/api/agents/<int:agent_id>/assignments", methods=["GET"])
@login_required(role=["admin", "agent"])
def get_agent_assignments(agent_id):
    agent = User.query.filter_by(id=agent_id, role="agent").first()
    if not agent:
        return jsonify({"message": "Agent not found"}), 404
    
    assignments = agent.assignments
    return jsonify([assignment.serialize() for assignment in assignments])

@agent_bp.route("/api/agent/notifications", methods=["GET"])

def get_agent_notifications():
    agent_id = session.get("user_id")
    if not agent_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    agent = User.query.get(agent_id)
    if not agent:
        return jsonify({"error": "Agent not found"}), 404
    
    try:
        # Get all notifications from the detection log
        notifications = DetectionLog.query.order_by(DetectionLog.timestamp.desc()).all()
        
        # Filter for ones assigned to this agent
        agent_notifications = []
        for notification in notifications:
            details = notification.details or {}
            assigned_agent = details.get('assigned_agent')
            
            # Check if this notification is assigned to the current agent
            if assigned_agent and assigned_agent.lower() == agent.username.lower():
                agent_notifications.append({
                    "id": notification.id,
                    "event_type": notification.event_type,
                    "timestamp": notification.timestamp.isoformat(),
                    "details": notification.details,
                    "read": notification.read,
                    "room_url": notification.room_url,
                    "assigned_agent": assigned_agent
                })
        
        return jsonify(agent_notifications), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@agent_bp.route("/api/agent/notifications/<int:notification_id>/read", methods=["PUT"])

def mark_agent_notification_read(notification_id):
    agent_id = session.get("user_id")
    if not agent_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    agent = User.query.get(agent_id)
    if not agent:
        return jsonify({"error": "Agent not found"}), 404
    
    try:
        notification = DetectionLog.query.get(notification_id)
        if not notification:
            return jsonify({"message": "Notification not found"}), 404
        
        # Verify this notification is assigned to this agent
        details = notification.details or {}
        assigned_agent = details.get('assigned_agent')
        
        if not assigned_agent or assigned_agent.lower() != agent.username.lower():
            return jsonify({"error": "Notification not assigned to this agent"}), 403
        
        notification.read = True
        db.session.commit()
        return jsonify({"message": "Notification marked as read"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@agent_bp.route("/api/agent/notifications/read-all", methods=["PUT"])

def mark_all_agent_notifications_read():
    agent_id = session.get("user_id")
    if not agent_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    agent = User.query.get(agent_id)
    if not agent:
        return jsonify({"error": "Agent not found"}), 404
    
    try:
        # Get all notifications assigned to this agent
        notifications = DetectionLog.query.all()
        
        count = 0
        for notification in notifications:
            details = notification.details or {}
            assigned_agent = details.get('assigned_agent')
            
            if assigned_agent and assigned_agent.lower() == agent.username.lower() and not notification.read:
                notification.read = True
                count += 1
        
        db.session.commit()
        return jsonify({"message": f"Marked {count} notifications as read"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500