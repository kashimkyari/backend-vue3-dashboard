from flask import Blueprint, request, jsonify, send_from_directory, session, current_app
from models import Stream
from utils import login_required
from extensions import db
from services.communication_service import communication_service
import os

detection_bp = Blueprint('detection', __name__)

def get_stream_url(stream):
    """Get the appropriate stream URL (M3U8 or room URL) from a Stream object."""
    for attr in dir(stream):
        if attr.endswith('_m3u8_url'):
            stream_url = getattr(stream, attr, '')
            if stream_url:
                return stream_url
    return getattr(stream, 'stream_url', getattr(stream, 'room_url', ''))

@detection_bp.route("/detection-images/<filename>")
def serve_detection_image(filename):
    return send_from_directory("detections", filename)

@detection_bp.route("/api/detect", methods=["POST"])
def unified_detect():
    data = request.get_json()
    text = data.get("text", "")
    visual_frame = data.get("visual_frame", None)
    visual_results = []
    
    if visual_frame:
        try:
            from monitoring import process_video_frame
            import numpy as np
            visual_results = process_video_frame(np.array(visual_frame), "unified_detect")
        except ImportError:
            current_app.logger.warning("Monitoring module not available in main app")
            visual_results = []
    
    try:
        from monitoring import process_chat_messages
        chat_results = process_chat_messages([{"message": text}], "unified_detect")
    except ImportError:
        current_app.logger.warning("Monitoring module not available in main app")
        chat_results = []
    
    return jsonify({
        "chat": chat_results,
        "visual": visual_results
    })

@detection_bp.route("/api/trigger-detection", methods=["POST"])
def trigger_detection():
    current_app.logger.info("Received request to /api/trigger-detection")
    data = request.get_json()
    current_app.logger.info(f"Request data: {data}")
    
    stream_id = data.get("stream_id")
    stop = data.get("stop", False)
    
    if not stream_id:
        return jsonify({"error": "Missing stream_id"}), 400

    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # Use communication service to contact monitor app
        response_data = communication_service.post_to_monitor(
            "/api/monitor/trigger-detection",
            {"stream_id": stream_id, "stop": stop}
        )
        
        if "error" in response_data:
            if response_data.get("fallback"):
                # Fallback to database-only operation
                return handle_detection_fallback(stream_id, stop)
            else:
                return jsonify(response_data), 500
        
        return jsonify(response_data), 200
        
    except Exception as e:
        current_app.logger.error(f"Error in trigger_detection: {str(e)}")
        return handle_detection_fallback(stream_id, stop)

def handle_detection_fallback(stream_id, stop):
    """Fallback handling when monitor app is unavailable."""
    current_app.logger.info(f"Using fallback mode for stream {stream_id}")
    
    try:
        stream = Stream.query.get_or_404(stream_id)
        stream_url = get_stream_url(stream)
        
        if stop:
            stream.is_monitored = False
            db.session.commit()
            return jsonify({
                "message": "Detection stopped (fallback mode)",
                "stream_id": stream_id,
                "active": False,
                "status": stream.status or "unknown",
                "isDetecting": False,
                "isDetectionLoading": False,
                "detectionError": "Monitor service unavailable"
            }), 200
        else:
            if stream.status == 'offline':
                return jsonify({
                    "error": "Cannot start detection for offline stream",
                    "stream_id": stream_id,
                    "active": False,
                    "status": "offline",
                    "isDetecting": False,
                    "isDetectionLoading": False,
                    "detectionError": "Stream is offline"
                }), 400
            
            return jsonify({
                "error": "Monitor service unavailable",
                "stream_id": stream_id,
                "active": False,
                "status": stream.status or "unknown",
                "isDetecting": False,
                "isDetectionLoading": False,
                "detectionError": "Monitor service unavailable"
            }), 503
            
    except Exception as e:
        current_app.logger.error(f"Fallback handling failed: {str(e)}")
        return jsonify({
            "error": f"Fallback handling failed: {str(e)}",
            "stream_id": stream_id,
            "active": False,
            "status": "unknown",
            "isDetecting": False,
            "isDetectionLoading": False,
            "detectionError": str(e)
        }), 500

@detection_bp.route("/api/detection-status/<int:stream_id>", methods=["GET"])
def detection_status(stream_id):
    try:
        # Try to get status from monitor app
        response_data = communication_service.get_from_monitor(
            f"/api/monitor/detection-status/{stream_id}"
        )
        
        if "error" not in response_data:
            return jsonify(response_data), 200
        
        # Fallback to database query
        current_app.logger.info(f"Using fallback for detection status of stream {stream_id}")
        stream = Stream.query.get_or_404(stream_id)
        stream_url = get_stream_url(stream)
        is_active = stream.is_monitored and stream.status != 'offline'
        stream_status = getattr(stream, 'status', 'unknown')
        
        return jsonify({
            "stream_id": stream_id,
            "stream_url": stream_url,
            "active": is_active,
            "status": stream_status,
            "isDetecting": is_active,
            "isDetectionLoading": False,
            "detectionError": "Monitor service unavailable" if response_data.get("fallback") else None
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error in detection_status: {str(e)}")
        return jsonify({
            "error": f"Failed to get detection status: {str(e)}",
            "stream_id": stream_id,
            "active": False,
            "status": "unknown",
            "isDetecting": False,
            "isDetectionLoading": False,
            "detectionError": str(e)
        }), 500

@detection_bp.route("/api/streams/<int:stream_id>/status", methods=["POST"])
def update_stream_status(stream_id):
    """Update the status of a stream."""
    data = request.get_json()
    if not data or "status" not in data:
        return jsonify({"error": "Missing status in request body"}), 400

    status = data.get("status")
    if status not in ["online", "offline", "monitoring"]:
        return jsonify({"error": "Invalid status value"}), 400

    stream = Stream.query.get(stream_id)
    if not stream:
        return jsonify({"error": "Stream not found"}), 404

    try:
        stream.status = status
        
        if status == 'offline':
            # Notify monitoring app to stop detection
            try:
                communication_service.post_to_monitor(
                    "/api/monitor/trigger-detection",
                    {"stream_id": stream_id, "stop": True}
                )
            except Exception as e:
                current_app.logger.warning(f"Could not notify monitor app: {str(e)}")
        
        db.session.commit()
        current_app.logger.info(f"Stream {stream_id} status updated to {status}")
        
        return jsonify({
            "message": "Stream status updated successfully",
            "stream_id": stream_id,
            "status": stream.status
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating stream {stream_id} status: {str(e)}")
        return jsonify({"error": f"Failed to update stream status: {str(e)}"}), 500
