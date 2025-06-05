import asyncio
import time
import requests
import threading
import hashlib
import json
from flask import current_app
from extensions import db
from models import User, DetectionLog, ChatMessage, Stream, Assignment
from utils.notifications import emit_notification, emit_message_update
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import logging
import os
from telegram import Bot
from telegram.error import TelegramError
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SmartAlertFilter:
    """Smart alert filtering to prevent duplicate notifications"""
    
    def __init__(self):
        self.alert_cache = {}
        self.cache_lock = threading.Lock()
        self.cleanup_interval = 300  # 5 minutes
        self.default_cooldown = {
            'stream_status_update': 300,  # 5 minutes
            'stream_assigned': 60,       # 1 minute
            'stream_unassigned': 60,     # 1 minute
            'detection_alert': 30,       # 30 seconds
            'system_alert': 60,          # 1 minute
            'default': 30                # 30 seconds
        }
        self.priority_multipliers = {
            'high': 0.5,     # Reduce cooldown for high priority
            'normal': 1.0,   # Normal cooldown
            'low': 2.0       # Increase cooldown for low priority
        }
        
    def generate_alert_key(self, user_id, event_type, details, channels=None):
        """Generate a unique key for alert deduplication"""
        content_items = [
            str(user_id),
            event_type,
            details.get('room_url', ''),
            details.get('streamer_username', ''),
            details.get('platform', ''),
            details.get('message', ''),
            ','.join(sorted(channels or []))
        ]
        
        if event_type == 'stream_status_update':
            content_items.append(details.get('status', ''))
            
        content_string = '|'.join(str(item) for item in content_items)
        return hashlib.md5(content_string.encode()).hexdigest()
    
    def should_send_alert(self, user_id, event_type, details, channels, priority='normal'):
        """Determine if an alert should be sent based on smart filtering"""
        with self.cache_lock:
            alert_key = self.generate_alert_key(user_id, event_type, details, channels)
            current_time = datetime.utcnow()
            
            base_cooldown = self.default_cooldown.get(event_type, self.default_cooldown['default'])
            priority_multiplier = self.priority_multipliers.get(priority, 1.0)
            cooldown_seconds = int(base_cooldown * priority_multiplier)
            
            if alert_key in self.alert_cache:
                last_sent = self.alert_cache[alert_key]['timestamp']
                time_diff = (current_time - last_sent).total_seconds()
                
                if time_diff < cooldown_seconds:
                    self.alert_cache[alert_key]['attempts'] += 1
                    logger.info(f"Alert suppressed (attempt #{self.alert_cache[alert_key]['attempts']}): "
                              f"{event_type} for user {user_id}, cooldown: {cooldown_seconds}s, "
                              f"time_since_last: {time_diff:.1f}s")
                    return False
                else:
                    self.alert_cache[alert_key].update({
                        'timestamp': current_time,
                        'attempts': 1,
                        'details': details
                    })
                    logger.info(f"Alert allowed after cooldown: {event_type} for user {user_id}")
                    return True
            else:
                self.alert_cache[alert_key] = {
                    'timestamp': current_time,
                    'attempts': 1,
                    'event_type': event_type,
                    'user_id': user_id,
                    'details': details,
                    'channels': channels
                }
                logger.info(f"New alert allowed: {event_type} for user {user_id}")
                return True
    
    def cleanup_expired_alerts(self):
        """Remove expired alerts from cache"""
        with self.cache_lock:
            current_time = datetime.utcnow()
            expired_keys = []
            
            for key, alert_data in self.alert_cache.items():
                time_diff = (current_time - alert_data['timestamp']).total_seconds()
                max_cooldown = max(self.default_cooldown.values()) * 2
                
                if time_diff > max_cooldown:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self.alert_cache[key]
            
            if expired_keys:
                logger.info(f"Cleaned up {len(expired_keys)} expired alert cache entries")
    
    def get_cache_stats(self):
        """Get statistics about the alert cache"""
        with self.cache_lock:
            total_alerts = len(self.alert_cache)
            suppressed_alerts = sum(1 for alert in self.alert_cache.values() if alert['attempts'] > 1)
            return {
                'total_cached_alerts': total_alerts,
                'suppressed_alerts': suppressed_alerts,
                'suppression_rate': f"{(suppressed_alerts/total_alerts*100):.1f}%" if total_alerts > 0 else "0%"
            }

class NotificationService:
    socketio = None
    app = None
    scheduler = None
    alert_filter = SmartAlertFilter()
    stream_status_cache = {}
    status_aggregation_cache = {}

    @staticmethod
    def init(app):
        """Initialize NotificationService with Flask app and SocketIO."""
        try:
            NotificationService.app = app
            NotificationService.scheduler = BackgroundScheduler()
            logger.info("NotificationService initialized with Flask app, SocketIO, and scheduler")
        except Exception as e:
            logger.error(f"Failed to initialize NotificationService: {str(e)}")
            raise

    @staticmethod
    def start_scheduler(detection_only=False):
        """Start the background scheduler for stream status monitoring and cache cleanup."""
        try:
            if not NotificationService.scheduler.running:
                if not detection_only:
                    NotificationService.scheduler.add_job(
                        NotificationService.check_stream_statuses,
                        trigger=IntervalTrigger(seconds=int(os.getenv('STREAM_STATUS_CHECK_INTERVAL', 60))),
                        id='stream_status_check',
                        replace_existing=True
                    )
                
                NotificationService.scheduler.add_job(
                    NotificationService.alert_filter.cleanup_expired_alerts,
                    trigger=IntervalTrigger(seconds=NotificationService.alert_filter.cleanup_interval),
                    id='alert_cache_cleanup',
                    replace_existing=True
                )
                
                NotificationService.scheduler.start()
                logger.info("Background scheduler started for stream status monitoring and alert cache cleanup")
            else:
                logger.info("Scheduler already running")
        except Exception as e:
            logger.error(f"Failed to start scheduler: {str(e)}")
            raise

    @staticmethod
    def check_stream_statuses():
        """Periodically check the status of all streams and aggregate notifications."""
        start_time = time.time()
        try:
            with NotificationService.app.app_context():
                streams = Stream.query.all()
                logger.debug(f"Checking {len(streams)} streams")
                
                status_changes = []
                
                for stream in streams:
                    new_status = NotificationService.get_stream_status(stream)
                    old_status = NotificationService.stream_status_cache.get(stream.id, stream.status)
                    
                    if new_status != old_status:
                        logger.info(f"Stream {stream.streamer_username} status changed from {old_status} to {new_status}")
                        stream.status = new_status
                        stream.is_monitored = new_status == 'monitoring'
                        
                        status_changes.append({
                            'stream': stream,
                            'old_status': old_status,
                            'new_status': new_status
                        })
                        
                        NotificationService.stream_status_cache[stream.id] = new_status
                
                if status_changes:
                    db.session.commit()
                    
                    for change in status_changes:
                        NotificationService.notify_stream_status_change(
                            change['stream'], 
                            change['old_status'], 
                            change['new_status']
                        )
                
                logger.debug(f"Stream status check completed in {time.time() - start_time:.2f} seconds")
        except Exception as e:
            logger.error(f"Error checking stream statuses: {str(e)}")
            db.session.rollback()
        finally:
            elapsed_time = time.time() - start_time
            if elapsed_time > 60:
                logger.warning(f"Stream status check took {elapsed_time:.2f} seconds, exceeding interval")

    @staticmethod
    def get_stream_status(stream):
        """Check the live status of a stream by querying its m3u8 URL."""
        try:
            if stream.type.lower() == 'chaturbate' and stream.chaturbate_m3u8_url:
                url = stream.chaturbate_m3u8_url
            elif stream.type.lower() == 'stripchat' and stream.stripchat_m3u8_url:
                url = stream.stripchat_m3u8_url
            else:
                url = stream.room_url

            response = requests.head(url, timeout=5)
            return 'online' if response.status_code == 200 else 'offline'
        except requests.RequestException as e:
            logger.warning(f"Failed to check status for stream {stream.streamer_username}: {str(e)}")
            return 'offline'

    @staticmethod
    def get_stream_assignment(room_url):
        """Get assignment info for a stream."""
        try:
            stream = Stream.query.filter_by(room_url=room_url).first()
            if not stream:
                from models import ChaturbateStream, StripchatStream
                cb_stream = ChaturbateStream.query.filter_by(chaturbate_m3u8_url=room_url).first()
                if cb_stream:
                    stream = Stream.query.get(cb_stream.id)
                else:
                    sc_stream = StripchatStream.query.filter_by(stripchat_m3u8_url=room_url).first()
                    if sc_stream:
                        stream = Stream.query.get(sc_stream.id)
            
            if not stream:
                logger.warning(f"No stream found for URL: {room_url}")
                return None, None
            
            query = Assignment.query.filter_by(stream_id=stream.id, status='active')
            assignment = query.first()
            return (assignment.id, assignment.agent_id) if assignment else (None, None)
        except Exception as e:
            logger.error(f"Error fetching assignment for stream {room_url}: {e}")
            return None, None

    @staticmethod
    def fetch_agent_username(agent_id):
        """Fetch a single agent's username and cache it."""
        from utils.notifications import agent_cache
        if agent_id in agent_cache:
            return agent_cache[agent_id]
        try:
            agent = User.query.get(agent_id)
            if agent:
                username = agent.username or f"Agent {agent_id}"
                agent_cache[agent_id] = username
                logger.debug(f"Cached username for agent {agent_id}: {username}")
                return username
            else:
                logger.warning(f"Agent {agent_id} not found")
                agent_cache[agent_id] = f"Agent {agent_id}"
                return agent_cache[agent_id]
        except Exception as e:
            logger.error(f"Error fetching username for agent {agent_id}: {e}")
            agent_cache[agent_id] = f"Agent {agent_id}"
            return agent_cache[agent_id]

    @staticmethod
    async def send_telegram_notification(user, event_type, details, platform, streamer, is_image=False, image_data=None):
        """Send a Telegram notification with retry logic."""
        telegram_token = os.getenv('TELEGRAM_TOKEN')
        if not telegram_token or not user.telegram_chat_id:
            logger.error(f"Telegram not configured for user {user.username}")
            return

        bot = Bot(token=telegram_token)
        room_url = details.get('room_url')
        _, agent_id = NotificationService.get_stream_assignment(room_url) if room_url else (None, None)
        assigned_agent = NotificationService.fetch_agent_username(agent_id) if agent_id else details.get('assigned_agent', 'Unassigned')

        message = (
            f"🚨 ALERT: {event_type.replace('_', ' ').title()}\n"
            f"Platform: {platform or 'Unknown'}\n"
            f"Streamer: {streamer or 'Unknown'}\n"
            f"Assigned Agent: {assigned_agent}\n"
            f"URL: {room_url or 'No URL provided'}\n"
            f"Details: {details.get('message', 'No details provided')}"
        )

        max_retries = int(os.getenv('TELEGRAM_MAX_RETRIES', 3))
        retry_delay = int(os.getenv('TELEGRAM_RETRY_DELAY', 2))

        for attempt in range(max_retries):
            try:
                if is_image and image_data:
                    from io import BytesIO
                    photo_file = BytesIO(image_data)
                    photo_file.seek(0)
                    await bot.send_photo(chat_id=user.telegram_chat_id, photo=photo_file, caption=message)
                else:
                    await bot.send_message(chat_id=user.telegram_chat_id, text=message)
                logger.info(f"Sent Telegram message to {user.telegram_chat_id} for {event_type}")
                break
            except TelegramError as te:
                if attempt < max_retries - 1:
                    logger.warning(f"Telegram attempt {attempt + 1} failed: {str(te)}. Retrying...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"Failed to send Telegram after {max_retries} attempts: {str(te)}")
            except Exception as e:
                logger.error(f"Unexpected error sending Telegram: {str(e)}")
                break

    @staticmethod
    def send_user_notification(user, event_type, details, room_url=None, platform=None, streamer=None, is_image=False, image_data=None, priority='normal'):
        """Send notifications to a user based on their preferences with smart filtering."""
        if not user.receive_updates:
            logger.info(f"User {user.username} has notifications disabled")
            return

        allowed_agent_events = ['stream_assigned', 'stream_unassigned']
        if user.role == 'agent' and event_type not in allowed_agent_events:
            logger.info(f"Skipping notification for agent {user.username} for event {event_type}")
            return

        if event_type == 'stream_status_update' and user.role != 'admin':
            logger.info(f"Skipping stream status update for non-admin user {user.username}")
            return

        available_channels = []
        if user.email:
            available_channels.append('email')
        if user.telegram_chat_id:
            available_channels.append('telegram')
        available_channels.append('in_app')

        if not NotificationService.alert_filter.should_send_alert(
            user.id, event_type, details, available_channels, priority
        ):
            return

        channels_sent = []
        
        try:
            NotificationService.create_in_app_notification(user, event_type, details, room_url, platform, streamer)
            channels_sent.append('in_app')
        except Exception as e:
            logger.error(f"Failed to send in-app notification to {user.username}: {str(e)}")

        if user.email:
            try:
                NotificationService.send_email_notification(user, event_type, details, platform, streamer)
                channels_sent.append('email')
            except Exception as e:
                logger.error(f"Failed to send email notification to {user.username}: {str(e)}")

        if user.telegram_chat_id:
            try:
                asyncio.run(NotificationService.send_telegram_notification(
                    user, event_type, details, platform, streamer, is_image, image_data
                ))
                channels_sent.append('telegram')
            except Exception as e:
                logger.error(f"Failed to send Telegram notification to {user.username}: {str(e)}")

        if channels_sent:
            logger.info(f"Sent notifications to {user.username} via {', '.join(channels_sent)} for {event_type}")

    @staticmethod
    def create_in_app_notification(user, event_type, details, room_url=None, platform=None, streamer=None):
        """Create an in-app notification."""
        try:
            assignment_id, agent_id = NotificationService.get_stream_assignment(room_url) if room_url else (None, None)
            assigned_agent = user.username if user.role == 'agent' else NotificationService.fetch_agent_username(agent_id) if agent_id else 'Unassigned'

            notification = DetectionLog(
                event_type=event_type,
                room_url=room_url or details.get('room_url', ''),
                details={
                    **details,
                    'platform': platform or details.get('platform', 'Unknown'),
                    'streamer_name': streamer or details.get('streamer_username', 'Unknown'),
                    'assigned_agent': assigned_agent,
                },
                timestamp=datetime.utcnow(),
                read=False,
                assigned_agent=agent_id,
                assignment_id=assignment_id,
                detection_image=details.get('image_data') if details.get('image_data') else None
            )
            db.session.add(notification)
            db.session.commit()

            notification_data = {
                "id": notification.id,
                "event_type": event_type,
                "timestamp": notification.timestamp.isoformat(),
                "details": notification.details,
                "read": notification.read,
                "room_url": notification.room_url,
                "streamer": notification.details.get('streamer_name', 'Unknown'),
                "platform": notification.details.get('platform', 'Unknown'),
                "assigned_agent": assigned_agent,
            }
            emit_notification(notification_data)
            if event_type == 'stream_created':
                logger.info(f"Sent notifications to {user.username} via in_app for stream_created")
            else:
                logger.info(f"Created in-app notification for {event_type} to user {user.username}")
        except Exception as e:
            logger.error(f"Failed to create in-app notification: {str(e)}")
            db.session.rollback()

    @staticmethod
    def send_email_notification(user, event_type, details, platform, streamer):
        """Send an email notification."""
        try:
            room_url = details.get('room_url')
            _, agent_id = NotificationService.get_stream_assignment(room_url) if room_url else (None, None)
            assigned_agent = NotificationService.fetch_agent_username(agent_id) if agent_id else details.get('assigned_agent', 'Unassigned')

            msg = MIMEText(
                f"ALERT: {event_type.replace('_', ' ').title()} Alert\n"
                f"Platform: {platform or 'Unknown'}\n"
                f"Streamer: {streamer or 'Unknown'}\n"
                f"Assigned Agent: {assigned_agent}\n"
                f"Details: {details.get('message', 'No details provided')}\n"
                f"URL: {room_url or 'No URL provided'}"
            )
            msg['Subject'] = f"[StreamMonitor] {event_type.replace('_', ' ').title()} Notification"
            msg['From'] = os.getenv('MAIL_DEFAULT_SENDER', 'noreply@jetcamstudio.com')
            msg['To'] = user.email

            max_retries = int(os.getenv('MAIL_MAX_RETRIES', 3))
            retry_delay = int(os.getenv('MAIL_RETRY_DELAY', 2))

            for attempt in range(max_retries):
                try:
                    with smtplib.SMTP(os.getenv('MAIL_SERVER', 'smtp.gmail.com'), int(os.getenv('MAIL_PORT', 587))) as server:
                        if os.getenv('MAIL_USE_TLS', 'True').lower() == 'true':
                            server.starttls()
                        server.login(os.getenv('MAIL_USERNAME'), os.getenv('MAIL_PASSWORD'))
                        server.send_message(msg)
                    logger.info(f"Sent email to {user.email} for {event_type}")
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Email attempt {attempt + 1} failed: {str(e)}. Retrying...")
                        time.sleep(retry_delay)
                    else:
                        logger.error(f"Failed to send email to {user.email}: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to send email to {user.email}: {str(e)}")

    @staticmethod
    def notify_admins(event_type, details, room_url=None, platform=None, streamer=None, priority='normal'):
        """Notify all admins."""
        admins = User.query.filter_by(role='admin', receive_updates=True).all()
        room_url = room_url or details.get('room_url')
        _, agent_id = NotificationService.get_stream_assignment(room_url) if room_url else (None, None)
        details['assigned_agent'] = NotificationService.fetch_agent_username(agent_id) if agent_id else 'Unassigned'
        
        for admin in admins:
            if admin.role != 'admin':
                continue
            NotificationService.send_user_notification(
                admin, event_type, details, room_url, platform, streamer, priority=priority
            )

    @staticmethod
    def notify_assignment(agent, stream, assigner, notes=None, priority='normal'):
        """Notify an agent about a new assignment."""
        details = {
            'message': f"New stream assignment: {stream.streamer_username}",
            'room_url': stream.room_url,
            'streamer_username': stream.streamer_username,
            'platform': stream.type,
            'assigned_by': assigner.username if assigner else 'Admin',
            'assigned_agent': agent.username,
            'notes': notes or '',
            'priority': priority,
        }
        NotificationService.send_user_notification(
            agent, 'stream_assigned', details, stream.room_url, stream.type, stream.streamer_username, priority=priority
        )

        try:
            sys_msg = ChatMessage(
                sender_id=assigner.id if assigner else 1,
                receiver_id=agent.id,
                message=f"📡 New Stream Assignment: {stream.streamer_username}",
                details=details,
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
                "details": details
            })
            logger.info(f"Emitted assignment message for {stream.streamer_username} to agent {agent.username}")
        except Exception as e:
            logger.error(f"Failed to create assignment notification message: {str(e)}")
            db.session.rollback()

    @staticmethod
    def notify_unassignment(agent, stream, assigner):
        """Notify an agent about an unassignment."""
        details = {
            'message': f"Stream unassigned: {stream.streamer_username}",
            'room_url': stream.room_url,
            'streamer_username': stream.streamer_username,
            'platform': stream.type,
            'unassigned_by': assigner.username if assigner else 'Admin',
            'assigned_agent': agent.username
        }
        NotificationService.send_user_notification(
            agent, 'stream_unassigned', details, stream.room_url, stream.type, stream.streamer_username
        )

        try:
            sys_msg = ChatMessage(
                sender_id=assigner.id if assigner else 1,
                receiver_id=agent.id,
                message=f"📴 Stream Unassigned: {stream.streamer_username}",
                details=details,
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
                "details": details
            })
            logger.info(f"Emitted unassignment message for {stream.streamer_username} to agent {agent.username}")
        except Exception as e:
            logger.error(f"Failed to create unassignment notification message: {str(e)}")
            db.session.rollback()

    @staticmethod
    def notify_stream_status_change(stream, old_status, new_status):
        """Notify admins of a stream status change and trigger auto-monitoring if online."""
        details = {
            'message': f"Stream status changed from {old_status} to {new_status}",
            'room_url': stream.room_url,
            'streamer_username': stream.streamer_username,
            'platform': stream.type,
            'status': new_status
        }
        NotificationService.notify_admins(
            'stream_status_update',
            details,
            stream.room_url,
            stream.type,
            stream.streamer_username,
            priority='normal'
        )

        if new_status == 'online':
            try:
                from monitoring import auto_start_monitoring_on_online
                auto_start_monitoring_on_online(stream)
                logger.info(f"Triggered auto-monitoring for stream {stream.streamer_username}")
            except Exception as e:
                logger.error(f"Failed to trigger auto-monitoring for stream {stream.streamer_username}: {str(e)}")
                NotificationService.notify_admins(
                    'system_alert',
                    {
                        'message': f"Failed to auto-start monitoring for stream {stream.streamer_username}: {str(e)}",
                        'room_url': stream.room_url,
                        'streamer_username': stream.streamer_username,
                        'platform': stream.type
                    },
                    stream.room_url,
                    stream.type,
                    stream.streamer_username,
                    priority='high'
                )