from flask import Blueprint, request, jsonify, session, current_app
from werkzeug.utils import secure_filename
import os
from extensions import db
from models import ChatMessage, User, MessageAttachment
from utils import login_required
from datetime import datetime
from utils.notifications import emit_message_update

messaging_bp = Blueprint('messaging', __name__)

# --------------------------------------------------------------------
# Messaging Endpoints
# --------------------------------------------------------------------
@messaging_bp.route("/api/messages", methods=["POST"])
@login_required()
def send_message():
    data = request.get_json()

    # Validate required fields
    if not data:
        return jsonify({"error": "Request body is required"}), 400

    receiver_id = data.get('receiver_id')
    message_text = data.get('message')
    attachment_id = data.get('attachment_id')

    # Enhanced validation
    if not receiver_id or not isinstance(receiver_id, int):
        return jsonify({"error": "Valid receiver_id is required"}), 400
        
    if not message_text and not attachment_id:
        return jsonify({"error": "Message content or attachment required"}), 400

    try:
        new_message = ChatMessage(
            sender_id=session["user_id"],
            receiver_id=receiver_id,
            message=message_text or "",
            timestamp=datetime.utcnow(),
            is_system=False,
            read=False
        )
        
        # Link attachment if provided
        if attachment_id:
            attachment = MessageAttachment.query.get(attachment_id)
            if attachment and attachment.user_id == session["user_id"]:
                new_message.attachment_id = attachment_id
            
        db.session.add(new_message)
        db.session.commit()
        
        # Serialize after commit to ensure ID exists
        message_data = {
            "id": new_message.id,
            "sender_id": new_message.sender_id,
            "receiver_id": new_message.receiver_id,
            "message": new_message.message,
            "timestamp": new_message.timestamp.isoformat(),
            "is_system": new_message.is_system,
            "read": new_message.read
        }

        if new_message.attachment_id:
            message_data["attachment_id"] = new_message.attachment_id

        emit_message_update(message_data)
        
        return jsonify(message_data), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@messaging_bp.route("/api/messages/<int:receiver_id>", methods=["GET"])
@login_required()
def get_messages(receiver_id):
    user_id = session["user_id"]
    messages = ChatMessage.query.filter(
        ((ChatMessage.sender_id == user_id) & (ChatMessage.receiver_id == receiver_id)) |
        ((ChatMessage.sender_id == receiver_id) & (ChatMessage.receiver_id == user_id))
    ).order_by(ChatMessage.timestamp.asc()).all()
    return jsonify([msg.serialize() for msg in messages])

@messaging_bp.route("/api/online-users", methods=["GET"])
@login_required()
def get_online_users():
    try:
        agents = User.query.filter(User.role.in_(["agent", "admin"])).all()
        return jsonify([{
            "id": agent.id,
            "username": agent.username,
            "online": agent.online,
            "last_active": agent.last_active.isoformat() if agent.last_active else None
        } for agent in agents])
    except Exception as e:
        current_app.logger.error(f"Error fetching online users: {str(e)}")
        return jsonify({"error": "Failed to fetch online users due to internal error."}), 500


# routes.py
@messaging_bp.route("/api/messages/mark-read", methods=["PUT"])
@login_required()
def mark_messages_read():
    data = request.get_json()
    message_ids = data.get("messageIds", [])
    
    ChatMessage.query.filter(ChatMessage.id.in_(message_ids)).update({"read": True})
    db.session.commit()
    return jsonify({"message": f"Marked {len(message_ids)} messages as read"})

@messaging_bp.route("/api/messages/<int:agent_id>", methods=["GET"])
@login_required()
def get_agent_messages(agent_id):
    # Check if user has valid session
    if "user_role" not in session or "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
        
    # Authorization check
    if not (session['user_role'] == 'admin' or session['user_id'] == agent_id):
        return jsonify({"error": "Forbidden"}), 403

    try:
        messages = ChatMessage.query.filter(
            (ChatMessage.receiver_id == agent_id) |
            (ChatMessage.sender_id == agent_id)
        ).order_by(ChatMessage.timestamp.asc()).all()
        
        return jsonify([message.serialize() for message in messages])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@messaging_bp.route("/api/attachments/upload", methods=["POST"])
@login_required()
def upload_attachment():
    """Upload a file attachment"""
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
        
    try:
        # Get secure filename
        filename = secure_filename(file.filename)
        
        # Generate unique filename
        unique_filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{filename}"
        
        # Determine MIME type
        mime_type = file.content_type or 'application/octet-stream'
        
        # Create uploads directory if it doesn't exist
        uploads_dir = os.path.join(current_app.static_folder, 'uploads')
        os.makedirs(uploads_dir, exist_ok=True)
        
        # Save file
        file_path = os.path.join(uploads_dir, unique_filename)
        file.save(file_path)
        
        # Create file record in database
        attachment = MessageAttachment(
            filename=filename,
            path=f"/static/uploads/{unique_filename}",
            mime_type=mime_type,
            size=os.path.getsize(file_path),
            user_id=session["user_id"]
        )
        
        db.session.add(attachment)
        db.session.commit()
        
        return jsonify({
            "id": attachment.id,
            "url": attachment.path,
            "name": attachment.filename,
            "type": attachment.mime_type,
            "size": attachment.size
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@messaging_bp.route("/api/messages/<int:user_id>/unread-count", methods=["GET"])
@login_required()
def get_unread_count(user_id):
    """Get count of unread messages from a specific user"""
    current_user_id = session["user_id"]
    
    try:
        count = ChatMessage.query.filter_by(
            sender_id=user_id, 
            receiver_id=current_user_id,
            read=False
        ).count()
        
        return jsonify({"count": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@messaging_bp.route("/api/messages/<int:message_id>/mark-read", methods=["PUT"])
@login_required()
def mark_message_read(message_id):
    """Mark a single message as read"""
    try:
        # Find message and verify permissions
        message = ChatMessage.query.get(message_id)
        if not message:
            return jsonify({"error": "Message not found"}), 404
            
        if message.receiver_id != session["user_id"]:
            return jsonify({"error": "Unauthorized"}), 403
            
        message.read = True
        db.session.commit()
        
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500