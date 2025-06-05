import os
import json
import logging
from flask import current_app
from models import Log, User, Stream, Assignment, DetectionLog
from extensions import db
from services.notification_service import NotificationService
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Agent cache for usernames
agent_cache = {}
all_agents_fetched = False

def fetch_all_agents():
    """Fetch all agents and cache their usernames."""
    global all_agents_fetched
    if all_agents_fetched:
        return
    try:
        agents = User.query.filter_by(role='agent').all()
        for agent in agents:
            agent_cache[agent.id] = agent.username or f"Agent {agent.id}"
        all_agents_fetched = True
        logging.info("All agent usernames cached successfully.")
    except Exception as e:
        logging.error(f"Error fetching all agents: {e}")

def fetch_agent_username(agent_id):
    """Fetch a single agent's username and cache it."""
    return NotificationService.fetch_agent_username(agent_id)

def send_notifications(log_entry, detections=None, platform_name=None, streamer_name=None):
    """Sends notifications based on the log_entry from the unified detection API."""
    try:
        from config import create_app
        app = create_app()
        
        with app.app_context():
            details = log_entry.details or {}
            platform = platform_name if platform_name is not None else details.get('platform', 'Unknown Platform')
            streamer = streamer_name if streamer_name is not None else details.get('streamer_name', 'Unknown Streamer')

            # Get assignment for the stream
            assignment_id, agent_id = NotificationService.get_stream_assignment(log_entry.room_url) if log_entry.room_url else (None, None)
            
            # Determine recipients
            recipients = []
            # Admins receive all notifications
            admins = User.query.filter_by(role='admin', receive_updates=True).all()
            for admin in admins:
                recipients.append(admin)
            
            # Add agent if assigned
            if agent_id and isinstance(log_entry, DetectionLog) and log_entry.assigned_agent == agent_id:
                agent = User.query.get(agent_id)
                if agent and agent.receive_updates:
                    recipients.append(agent)
            
            if not recipients:
                logging.warning("No eligible recipients found; skipping notification.")
                return

            # Prepare notification details
            assigned_agent_username = fetch_agent_username(agent_id) if agent_id else 'Unassigned'
            details.update({'assigned_agent': assigned_agent_username})

            # Handle different event types
            if log_entry.event_type == 'object_detection':
                detections_list = detections or details.get('detections') or []
                confidence = detections_list[0].get('confidence') if detections_list else None
                conf_str = f"{(confidence * 100):.1f}%" if isinstance(confidence, (int, float)) else "N/A"
                detected_objects = ", ".join([d["class"] for d in detections_list]) if detections_list else "No details"
                message = (
                    f"🚨 **Object Detection Alert**\n"
                    f"🎥 Platform: {platform}\n"
                    f"📡 Streamer: {streamer}\n"
                    f"📌 Objects Detected: {detected_objects}\n"
                    f"🔍 Confidence: {conf_str}\n"
                    f"👤 Assigned Agent: {assigned_agent_username}"
                )
                for recipient in recipients:
                    if log_entry.detection_image:
                        NotificationService.send_telegram_notification(
                            recipient, log_entry.event_type, {**details, 'message': message}, 
                            log_entry.room_url, platform, streamer, is_image=True, image_data=log_entry.detection_image
                        )
                    else:
                        NotificationService.send_telegram_notification(
                            recipient, log_entry.event_type, {**details, 'message': message}, 
                            log_entry.room_url, platform, streamer
                        )

            elif log_entry.event_type == 'audio_detection':
                keyword = details.get('keyword', 'N/A')
                transcript = details.get('transcript', 'No transcript available.')
                message = (
                    f"🔊 **Audio Detection Alert**\n"
                    f"🎥 Platform: {platform}\n"
                    f"📡 Streamer: {streamer}\n"
                    f"🔑 Keyword: {keyword}\n"
                    f"📝 Transcript: {transcript[:300]}...\n"
                    f"👤 Assigned Agent: {assigned_agent_username}"
                )
                for recipient in recipients:
                    NotificationService.send_telegram_notification(
                        recipient, log_entry.event_type, {**details, 'message': message}, 
                        log_entry.room_url, platform, streamer
                    )

            elif log_entry.event_type == 'chat_detection':
                detections = details.get('detections', [{}])
                first_detection = detections[0] if detections else {}
                message = (
                    f"💬 **Chat Detection Alert**\n"
                    f"🎥 Platform: {platform}\n"
                    f"📡 Streamer: {streamer}\n"
                    f"👤 Sender: {first_detection.get('sender', 'Unknown')}\n"
                    f"🔍 Keywords: {', '.join(first_detection.get('keywords', []))}\n"
                    f"📝 Message: {first_detection.get('message', '')[:300]}...\n"
                    f"👤 Assigned Agent: {assigned_agent_username}"
                )
                for recipient in recipients:
                    NotificationService.send_telegram_notification(
                        recipient, log_entry.event_type, {**details, 'message': message}, 
                        log_entry.room_url, platform, streamer
                    )

            elif log_entry.event_type == 'video_notification':
                msg_detail = details.get('message', 'No additional details.')
                message = (
                    f"🎥 **Video Notification**\n"
                    f"🎥 Platform: {platform}\n"
                    f"📡 Streamer: {streamer}\n"
                    f"📝 Message: {msg_detail}\n"
                    f"👤 Assigned Agent: {assigned_agent_username}"
                )
                for recipient in recipients:
                    NotificationService.send_telegram_notification(
                        recipient, log_entry.event_type, {**details, 'message': message}, 
                        log_entry.room_url, platform, streamer
                    )

            else:
                message = (
                    f"🔔 **{log_entry.event_type.replace('_', ' ').title()}**\n"
                    f"🎥 Platform: {platform}\n"
                    f"📡 Streamer: {streamer}\n"
                    f"📌 Details: {json.dumps(details, indent=2)[:500]}...\n"
                    f"👤 Assigned Agent: {assigned_agent_username}"
                )
                for recipient in recipients:
                    NotificationService.send_telegram_notification(
                        recipient, log_entry.event_type, {**details, 'message': message}, 
                        log_entry.room_url, platform, streamer
                    )

    except Exception as e:
        logging.error(f"Notification error: {str(e)}", exc_info=True)