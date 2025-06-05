from datetime import datetime, timezone, timedelta
from extensions import db

data = []

class User(db.Model):
    """
    User model represents an application user, such as agents or administrators.
    """
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="agent", index=True)
    online = db.Column(db.Boolean, default=False, index=True)
    last_active = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        index=True
    )
    receive_updates = db.Column(db.Boolean, default=False)
    email = db.Column(db.String(120), nullable=False, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    telegram_username = db.Column(db.String(50), unique=True, nullable=True, index=True)
    telegram_chat_id = db.Column(db.String(50), nullable=True, index=True)

    assignments = db.relationship(
        'Assignment',
        back_populates='agent',
        foreign_keys='Assignment.agent_id',
        lazy='selectin',
        cascade="all, delete"
    )
    detection_logs = db.relationship(
        'DetectionLog',
        back_populates='assigned_user',
        foreign_keys='DetectionLog.assigned_agent',
        lazy='dynamic'
    )

    def __repr__(self):
        return f"<User {self.username}>"

    def serialize(self, minimal=False):
        if minimal:
            return {
                "id": self.id,
                "username": self.username,
                "role": self.role,
                "online": self.online
            }
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "online": self.online,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "telegram_username": self.telegram_username,
            "telegram_chat_id": self.telegram_chat_id
        }

class Stream(db.Model):
    """
    Stream model serves as a base class for different streaming platforms.
    Uses polymorphic identity to distinguish between Chaturbate and Stripchat streams.
    """
    __tablename__ = "streams"
    id = db.Column(db.Integer, primary_key=True)
    room_url = db.Column(db.String(300), unique=True, nullable=False, index=True)
    streamer_username = db.Column(db.String(100), index=True)
    type = db.Column(db.String(50), index=True)
    status = db.Column(db.String(20), default='offline', nullable=False, index=True)
    is_monitored = db.Column(db.Boolean, default=False, nullable=False, index=True)

    assignments = db.relationship('Assignment', back_populates='stream', lazy='selectin', cascade="all, delete")

    __mapper_args__ = {
        'polymorphic_on': type,
        'polymorphic_identity': 'stream',
    }

    __table_args__ = (
        db.Index('idx_streams_status_type', 'status', 'type'),
        db.Index('idx_streams_status_monitored', 'status', 'is_monitored'),
    )

    def __repr__(self):
        return f"<Stream {self.room_url}>"

    def serialize(self, include_relationships=True, minimal=False):
        if minimal:
            return {
                "id": self.id,
                "room_url": self.room_url,
                "streamer_username": self.streamer_username,
                "platform": self.type.capitalize() if self.type else None,
                "status": self.status,
                "is_monitored": self.is_monitored
            }
        data = {
            "id": self.id,
            "room_url": self.room_url,
            "streamer_username": self.streamer_username,
            "platform": self.type.capitalize() if self.type else None,
            "status": self.status,
            "is_monitored": self.is_monitored,
        }
        if include_relationships and hasattr(self, 'assignments'):
            data["assignments"] = [assignment.serialize(include_relationships=False, minimal=True) for assignment in self.assignments]
        return data

class ChaturbateStream(Stream):
    """
    ChaturbateStream model extends Stream for Chaturbate-specific streams.
    Stores the m3u8 URL, broadcaster_uid, and room_uid for Chaturbate.
    """
    __tablename__ = "chaturbate_streams"
    id = db.Column(db.Integer, db.ForeignKey("streams.id"), primary_key=True)
    chaturbate_m3u8_url = db.Column(db.String(300), nullable=True, index=True)
    broadcaster_uid = db.Column(db.String(50), nullable=True, index=True)
    room_uid = db.Column(db.String(50), nullable=True, index=True)

    __mapper_args__ = {
        'polymorphic_identity': 'chaturbate'
    }

    def __repr__(self):
        return f"<ChaturbateStream {self.room_url}>"

    def serialize(self, include_relationships=True, minimal=False):
        data = super().serialize(include_relationships=include_relationships, minimal=minimal)
        if minimal:
            return data
        data.update({
            "platform": "Chaturbate",
            "chaturbate_m3u8_url": self.chaturbate_m3u8_url,
            "broadcaster_uid": self.broadcaster_uid,
            "room_uid": self.room_uid,
        })
        return data

class StripchatStream(Stream):
    """
    StripchatStream model extends Stream for Stripchat-specific streams.
    Stores the m3u8 URL for Stripchat.
    """
    __tablename__ = "stripchat_streams"
    id = db.Column(db.Integer, db.ForeignKey("streams.id"), primary_key=True)
    stripchat_m3u8_url = db.Column(db.String(300), nullable=True, index=True)

    __mapper_args__ = {
        'polymorphic_identity': 'stripchat'
    }

    def __repr__(self):
        return f"<StripchatStream {self.room_url}>"

    def serialize(self, include_relationships=True, minimal=False):
        data = super().serialize(include_relationships=include_relationships, minimal=minimal)
        if minimal:
            return data
        data.update({
            "platform": "Stripchat",
            "stripchat_m3u8_url": self.stripchat_m3u8_url,
        })
        return data

class Assignment(db.Model):
    """
    Assignment model links a User (agent) with a Stream, with additional metadata.
    """
    __tablename__ = "assignments"
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    stream_id = db.Column(db.Integer, db.ForeignKey('streams.id', ondelete='CASCADE'), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    assigned_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    notes = db.Column(db.Text, nullable=True)
    priority = db.Column(db.String(20), default='normal', index=True)
    status = db.Column(db.String(20), default='active', index=True)
    assignment_metadata = db.Column(db.JSON, nullable=True)

    agent = db.relationship('User', foreign_keys=[agent_id], back_populates='assignments', lazy='selectin')
    stream = db.relationship('Stream', back_populates='assignments', lazy='selectin')
    assigner = db.relationship('User', foreign_keys=[assigned_by], lazy='selectin')

    __table_args__ = (
        db.Index('idx_assignment_agent_stream', 'agent_id', 'stream_id'),
        db.Index('idx_assignment_status', 'status'),
    )

    def __repr__(self):
        agent_username = self.agent.username if self.agent else "Unassigned"
        return f"<Assignment Agent:{agent_username} Stream:{self.stream_id}>"

    def serialize(self, include_relationships=True, minimal=False):
        if minimal:
            return {
                "id": self.id,
                "agent_id": self.agent_id,
                "stream_id": self.stream_id,
                "status": self.status
            }
        data = {
            "id": self.id,
            "agent_id": self.agent_id,
            "stream_id": self.stream_id,
            "created_at": self.created_at.isoformat(),
            "assigned_by": self.assigned_by,
            "notes": self.notes,
            "priority": self.priority,
            "status": self.status,
            "assignment_metadata": self.assignment_metadata or {},
            "streamer_username": self.stream.streamer_username if self.stream else None
        }
        if include_relationships:
            if self.agent:
                data["agent"] = self.agent.serialize(minimal=True)
            else:
                data["agent"] = None
            if self.stream:
                data["stream"] = self.stream.serialize(include_relationships=False, minimal=True)
            else:
                data["stream"] = None
            if self.assigner:
                data["assigner"] = self.assigner.serialize(minimal=True)
            else:
                data["assigner"] = None
        return data

class Log(db.Model):
    """
    Log model records events such as detections, video notifications, and chat events.
    """
    __tablename__ = "logs"
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    room_url = db.Column(db.String(300), index=True)
    event_type = db.Column(db.String(50), index=True)
    details = db.Column(db.JSON)
    read = db.Column(db.Boolean, default=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)

    user = db.relationship('User', foreign_keys=[user_id], lazy='selectin')

    __table_args__ = (
        db.Index('idx_logs_room_event', 'room_url', 'event_type'),
        db.Index('idx_logs_timestamp_read', 'timestamp', 'read'),
    )

    def __repr__(self):
        return f"<Log {self.event_type} @ {self.room_url}>"

    def serialize(self, minimal=False):
        if minimal:
            return {
                "id": self.id,
                "timestamp": self.timestamp.isoformat(),
                "event_type": self.event_type,
                "read": self.read
            }
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "room_url": self.room_url,
            "event_type": self.event_type,
            "details": self.details,
            "read": self.read,
            "user_id": self.user_id,
        }

class ChatKeyword(db.Model):
    """
    ChatKeyword model stores keywords for flagging chat messages.
    """
    __tablename__ = "chat_keywords"
    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(100), unique=True, nullable=False, index=True)

    def __repr__(self):
        return f"<ChatKeyword {self.keyword}>"

    def serialize(self):
        return {"id": self.id, "keyword": self.keyword}

class FlaggedObject(db.Model):
    """
    FlaggedObject model stores objects to be flagged during detection,
    along with their confidence thresholds.
    """
    __tablename__ = "flagged_objects"
    id = db.Column(db.Integer, primary_key=True)
    object_name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    confidence_threshold = db.Column(db.Numeric(3, 2), default=0.8)

    def __repr__(self):
        return f"<FlaggedObject {self.object_name}>"

    def serialize(self):
        return {
            "id": self.id,
            "object_name": self.object_name,
            "confidence_threshold": float(self.confidence_threshold),
        }

class DetectionLog(db.Model):
    """
    DetectionLog model stores detection events, including the annotated image.
    Now with relationships to Assignment and User for assigned agent.
    """
    __tablename__ = "detection_logs"
    id = db.Column(db.Integer, primary_key=True)
    room_url = db.Column(db.String(255), nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    details = db.Column(db.JSON, nullable=True)
    detection_image = db.Column(db.LargeBinary, nullable=True)
    assigned_agent = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignments.id', ondelete='SET NULL'), nullable=True, index=True)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    sender_username = db.Column(db.String(100), nullable=True, index=True)
    read = db.Column(db.Boolean, default=False, index=True)

    assignment = db.relationship("Assignment", backref=db.backref("detection_logs", lazy="dynamic"))
    assigned_user = db.relationship("User", foreign_keys=[assigned_agent], back_populates='detection_logs', lazy='selectin')

    __table_args__ = (
        db.Index('idx_detection_logs_event_timestamp', 'event_type', 'timestamp'),
        db.Index('idx_detection_logs_assigned_agent', 'assigned_agent'),
    )

    def serialize(self, minimal=False):
        if minimal:
            return {
                "id": self.id,
                "room_url": self.room_url,
                "event_type": self.event_type,
                "timestamp": self.timestamp.isoformat(),
                "read": self.read
            }
        return {
            "id": self.id,
            "room_url": self.room_url,
            "event_type": self.event_type,
            "details": self.details,
            "assigned_agent": self.assigned_agent,
            "assignment_id": self.assignment_id,
            "timestamp": self.timestamp.isoformat(),
            "read": self.read,
            "sender_username": self.sender_username,
        }

class MessageAttachment(db.Model):
    """
    MessageAttachment model stores files attached to chat messages.
    """
    __tablename__ = "message_attachments"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(500), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    
    user = db.relationship("User")
    
    def __repr__(self):
        return f"<MessageAttachment {self.filename}>"
    
    def serialize(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "path": self.path,
            "mime_type": self.mime_type,
            "size": self.size,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "user_id": self.user_id
        }

class ChatMessage(db.Model):
    __tablename__ = "chat_messages"
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    read = db.Column(db.Boolean, default=False, index=True)
    is_system = db.Column(db.Boolean, default=False)
    details = db.Column(db.JSON)
    attachment_id = db.Column(db.Integer, db.ForeignKey('message_attachments.id', ondelete='SET NULL'), nullable=True)

    sender = db.relationship("User", foreign_keys=[sender_id])
    receiver = db.relationship("User", foreign_keys=[receiver_id])
    attachment = db.relationship("MessageAttachment", foreign_keys=[attachment_id])

    def serialize(self, minimal=False):
        if minimal:
            return {
                "id": self.id,
                "sender_id": self.sender_id,
                "receiver_id": self.receiver_id,
                "message": self.message,
                "timestamp": self.timestamp.isoformat() if self.timestamp else None,
                "read": self.read
            }
        result = {
            "id": self.id,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "message": self.message,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "read": self.read,
            "is_system": self.is_system,
            "details": self.details,
            "sender_username": self.sender.username if self.sender else None,
            "receiver_username": self.receiver.username if self.receiver else None
        }
        
        if self.attachment:
            result["attachment"] = {
                "id": self.attachment.id,
                "url": self.attachment.path,
                "name": self.attachment.filename,
                "type": self.attachment.mime_type,
                "size": self.attachment.size
            }
            
        return result

class PasswordReset(db.Model):
    __tablename__ = 'password_resets'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    token = db.Column(db.String(100), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('password_resets', lazy=True))
    
    def __repr__(self):
        return f'<PasswordReset {self.id} for user {self.user_id}>'
    
    def is_expired(self):
        return self.expires_at < datetime.utcnow()

class PasswordResetToken(db.Model):
    """
    Stores hashed password reset tokens with expiration and CASCADE deletion.
    """
    __tablename__ = 'password_reset_tokens'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    token_hash = db.Column(db.Text, unique=True, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref=db.backref('password_reset_tokens', lazy='dynamic'))

    __table_args__ = (
        db.Index('idx_password_reset_tokens_expires_at', 'expires_at'),
    )

    def __repr__(self):
        return f'<PasswordResetToken {self.id} for user {self.user_id}>'