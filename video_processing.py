import logging
import cv2
import numpy as np
from datetime import datetime, timedelta
from models import DetectionLog, Stream, ChaturbateStream, StripchatStream
from extensions import db
from utils.notifications import emit_notification
from dotenv import load_dotenv
import base64
import os
import gevent.lock

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# External dependencies - initialize with default values
_yolo_model = None
last_visual_alerts = {}

def initialize_video_globals(yolo_model=None):
    """Initialize global variables for YOLO model"""
    global _yolo_model
    _yolo_model = yolo_model
    logger.info("Video globals initialized")

def load_yolo_model(app):
    """Load the YOLO object detection model for CPU"""
    global _yolo_model
    
    with app.app_context():
        enable_video_monitoring = app.config.get('ENABLE_VIDEO_MONITORING', True)
        logger.info(f"ENABLE_VIDEO_MONITORING is set to: {enable_video_monitoring}")
        
        if not enable_video_monitoring:
            logger.info("Video monitoring disabled; skipping YOLO model loading")
            return None
            
        if _yolo_model is None:
            try:
                import torch
                import torchvision
                from ultralytics import YOLO
                logger.info(f"PyTorch version: {torch.__version__}")
                logger.info(f"Torchvision version: {torchvision.__version__}")
                _yolo_model = YOLO("yolov8n.pt", verbose=False)
                # _yolo_model.to('cpu')
                _yolo_model.eval()
                logger.info("yolov8n model loaded successfully on CPU")
            except ImportError as e:
                logger.error(f"Failed to import YOLO dependencies: {e}")
                _yolo_model = None
            except Exception as e:
                logger.error(f"Error loading YOLO model: {e}")
                try:
                    logger.info("Attempting to load fallback YOLO model")
                    _yolo_model = YOLO("yolo11n.pt", verbose=False)
                    _yolo_model.to('cpu')
                    _yolo_model.eval()
                    logger.info("Fallback YOLO11n model loaded successfully")
                except Exception as e2:
                    logger.error(f"Failed to load fallback YOLO model: {e2}")
                    _yolo_model = None
    return _yolo_model

def refresh_flagged_objects(app):
    """Retrieve flagged objects and confidence thresholds"""
    try:
        with app.app_context():
            from models import FlaggedObject
            objects = FlaggedObject.query.all()
            flagged = {obj.object_name.lower(): float(obj.confidence_threshold) for obj in objects}
        logger.debug(f"Retrieved {len(flagged)} flagged objects")
        return flagged
    except Exception as e:
        logger.error(f"Error retrieving flagged objects: {e}")
        return {}

def get_stream_info(stream_url, app):
    """Identify platform and streamer from URL"""
    try:
        with app.app_context():
            stream = Stream.query.filter_by(room_url=stream_url).first()
            if stream:
                return stream.type.lower(), stream.streamer_username
                
            cb_stream = ChaturbateStream.query.filter_by(chaturbate_m3u8_url=stream_url).first()
            if cb_stream:
                stream = Stream.query.get(cb_stream.id)
                return 'chaturbate', stream.streamer_username if stream else 'unknown'
                
            sc_stream = StripchatStream.query.filter_by(stripchat_m3u8_url=stream_url).first()
            if sc_stream:
                stream = Stream.query.get(sc_stream.id)
                return 'stripchat', stream.streamer_username if stream else 'unknown'
                
            return 'unknown', 'unknown'
            
    except Exception as e:
        logger.error(f"Error getting stream info: {e}")
        return 'unknown', 'unknown'

def get_stream_assignment(stream_url, app):
    """Get assignment info for a stream"""
    try:
        from sqlalchemy.orm import joinedload
        with app.app_context():
            stream = Stream.query.filter_by(room_url=stream_url).first()
            if not stream:
                cb_stream = ChaturbateStream.query.filter_by(chaturbate_m3u8_url=stream_url).first()
                if cb_stream:
                    stream = Stream.query.get(cb_stream.id)
                else:
                    sc_stream = StripchatStream.query.filter_by(stripchat_m3u8_url=stream_url).first()
                    if sc_stream:
                        stream = Stream.query.get(sc_stream.id)
            
            if not stream:
                logger.warning(f"No stream found for URL: {stream_url}")
                return None, None
            
            from models import Assignment
            query = Assignment.query.options(
                joinedload(Assignment.agent),
                joinedload(Assignment.stream)
            ).filter_by(stream_id=stream.id)
            
            assignments = query.all()
            
            if not assignments:
                logger.info(f"No assignments found for stream: {stream_url}")
                return None, None
            
            assignment = assignments[0]
            agent_id = assignment.agent_id
            return assignment.id, agent_id
    except Exception as e:
        logger.error(f"Error getting stream assignment: {e}")
        return None, None

# video_processing.py
def process_video_frame(frame, stream_url, app):
    """Process a video frame and return detections"""
    with app.app_context():  # Ensure context
        if not _yolo_model:
            return []
        
        try:
            flagged = refresh_flagged_objects(app)
            if not flagged:
                return []
                
            now = datetime.now()
            results = _yolo_model.predict(frame, verbose=False)
            detections = []
            
            for result in results:
                boxes = result.boxes
                for box in boxes:
                    try:
                        bbox = box.xyxy[0].cpu().numpy()
                        conf = float(box.conf[0].cpu().numpy())
                        cls_id = int(box.cls[0].cpu().numpy())
                        cls_name = _yolo_model.names.get(cls_id, str(cls_id)).lower()
                        
                        if cls_name not in flagged or conf < flagged[cls_name]:
                            continue
                        
                        if cls_name in last_visual_alerts.get(stream_url, {}):
                            last_alert = last_visual_alerts[stream_url][cls_name]
                            cooldown = app.config.get('VISUAL_ALERT_COOLDOWN', 60)
                            if (now - last_alert).total_seconds() < cooldown:
                                continue
                        
                        last_visual_alerts.setdefault(stream_url, {})[cls_name] = now
                        
                        detections.append({
                            "class": cls_name,
                            "confidence": conf,
                            "bbox": bbox.tolist(),
                            "timestamp": now.isoformat()
                        })
                        
                    except Exception as box_error:
                        logger.error(f"Error processing detection box: {box_error}")
                        continue
                
            return detections
            
        except Exception as e:
            logger.error(f"Error processing video frame: {e}")
            return []

def annotate_frame(frame, detections):
    """Draw detection boxes on frame"""
    try:
        annotated = frame.copy()
        for det in detections:
            x1, y1, x2, y2 = map(int, det["bbox"])
            label = f'{det["class"]} {det["confidence"]*100:.1f}%'
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(annotated, label, (x1, y1-10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        return annotated
    except Exception as e:
        logger.error(f"Error annotating frame: {e}")
        return frame

def log_video_detection(detections, frame, stream_url, app):
    """Log video detections with annotated frame"""
    with app.app_context():
        if not app.config.get('ENABLE_VIDEO_MONITORING', True) or not detections:
            return
            
        try:
            platform, streamer = get_stream_info(stream_url, app)
            assignment_id, agent_id = get_stream_assignment(stream_url, app)
            annotated = annotate_frame(frame, detections)
            
            success, buffer = cv2.imencode('.jpg', annotated)
            if not success:
                logger.error("Frame encoding failed")
                return
                
            image_b64 = base64.b64encode(buffer).decode('utf-8')
            details = {
                "detections": detections,
                "timestamp": datetime.now().isoformat(),
                "streamer_name": streamer,
                "platform": platform,
                "annotated_image": image_b64,
                "assigned_agent": agent_id
            }
            
            log_entry = DetectionLog(
                room_url=stream_url,
                event_type="object_detection",
                details=details,
                detection_image=buffer.tobytes(),
                timestamp=datetime.now(),
                assigned_agent=agent_id,
                assignment_id=assignment_id,
                read=False
            )
            db.session.add(log_entry)
            db.session.commit()
            
            notification_data = {
                "id": log_entry.id,
                "event_type": log_entry.event_type,
                "timestamp": log_entry.timestamp.isoformat(),
                "details": log_entry.details,
                "read": log_entry.read,
                "room_url": log_entry.room_url,
                "streamer": streamer,
                "platform": platform,
                "assigned_agent": "Unassigned" if not agent_id else "Agent"
            }
            emit_notification(notification_data)
            
        except Exception as e:
            logger.error(f"Error logging video detection: {e}")

def cleanup_video_resources(app):
    """Clean up video processing resources"""
    global _yolo_model
    try:
        if _yolo_model is not None:
            del _yolo_model
            _yolo_model = None
        logger.info("Video processing resources cleaned up")
    except Exception as e:
        logger.error(f"Error cleaning up video resources: {e}")