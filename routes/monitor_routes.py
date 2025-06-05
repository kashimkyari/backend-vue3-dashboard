# monitor_routes.py
from flask import Blueprint, request, jsonify, current_app
from models import Stream
from extensions import db
from monitoring import start_monitoring, stop_monitoring, stream_processors
from utils.notifications import emit_stream_update
from time import time
from monitoring import get_monitoring_status
from audio_processing import get_cache_stats
from chat_processing import get_performance_stats
import logging
import psutil
import gevent

monitor_bp = Blueprint('monitor', __name__)
logger = logging.getLogger(__name__)

def get_stream_url(stream):
    """Get the appropriate stream URL (M3U8 or room URL) from a Stream object."""
    for attr in dir(stream):
        if attr.endswith('_m3u8_url'):
            stream_url = getattr(stream, attr, '')
            if stream_url:
                return stream_url
    return getattr(stream, 'stream_url', getattr(stream, 'room_url', ''))

@monitor_bp.route("/api/monitor/trigger-detection", methods=["POST"])
def trigger_detection():
    """Handle detection trigger requests from the main app."""
    data = request.get_json()
    stream_id = data.get("stream_id")
    stop = data.get("stop", False)

    if not stream_id:
        return jsonify({"error": "Missing stream_id"}), 400

    stream = Stream.query.get(stream_id)
    if not stream:
        return jsonify({"error": "Stream not found"}), 404

    stream_url = get_stream_url(stream)

    if stop:
        if stream.is_monitored or stream_url in stream_processors:
            try:
                stop_monitoring(stream)
                stream.is_monitored = False
                db.session.commit()
                current_app.logger.info(f"Detection stopped for stream: {stream.id}")
                emit_stream_update({
                    'id': stream.id,
                    'url': stream_url,
                    'status': 'stopped',
                    'type': stream.type
                })
                return jsonify({
                    "message": "Detection stopped successfully",
                    "stream_id": stream.id,
                    "active": False,
                    "status": stream.status or "unknown",
                    "isDetecting": False,
                    "isDetectionLoading": False,
                    "detectionError": None
                }), 200
            except Exception as e:
                current_app.logger.error(f"Error stopping detection for stream: {stream.id}: {e}")
                return jsonify({
                    "error": f"Failed to stop detection: {e}",
                    "stream_id": stream.id,
                    "active": stream.is_monitored,
                    "status": stream.status or "unknown",
                    "isDetecting": stream.is_monitored,
                    "isDetectionLoading": False,
                    "detectionError": str(e)
                }), 500
        else:
            current_app.logger.info(f"No active detection found for stream: {stream.id}")
            return jsonify({
                "message": "No active detection found for this stream",
                "stream_id": stream.id,
                "active": False,
                "status": stream.status or "unknown",
                "isDetecting": False,
                "isDetectionLoading": False,
                "detectionError": None
            }), 200

    if stream.status == 'offline':
        current_app.logger.info(f"Cannot start detection for offline stream: {stream.id}")
        return jsonify({
            "error": "Cannot start detection for offline stream",
            "stream_id": stream.id,
            "active": False,
            "status": stream.status or "unknown",
            "isDetecting": False,
            "isDetectionLoading": False,
            "detectionError": "Stream is offline"
        }), 400

    if stream.is_monitored or stream_url in stream_processors:
        current_app.logger.info(f"Detection already running for stream: {stream.id}")
        return jsonify({
            "message": "Detection already running for this stream",
            "stream_id": stream.id,
            "active": True,
            "status": stream.status or "unknown",
            "isDetecting": True,
            "isDetectionLoading": False,
            "detectionError": None
        }), 409

    try:
        current_app.logger.info(f"Starting detection for stream: {stream.id}")
        if start_monitoring(current_app, stream):  # Corrected call
            stream.is_monitored = True
            db.session.commit()
            emit_stream_update({
                'id': stream.id,
                'url': stream_url,
                'status': 'monitoring',
                'type': stream.type
            })
            return jsonify({
                "message": "Detection started successfully",
                "stream_id": stream.id,
                "active": True,
                "status": stream.status or "unknown",
                "isDetecting": True,
                "isDetectionLoading": False,
                "detectionError": None
            }), 200
        else:
            current_app.logger.error(f"Failed to start monitoring for stream: {stream.id}")
            return jsonify({
                "error": "Failed to start monitoring",
                "stream_id": stream.id,
                "active": False,
                "status": stream.status or "unknown",
                "isDetecting": False,
                "isDetectionLoading": False,
                "detectionError": "Failed to start monitoring"
            }), 500
    except Exception as e:
        current_app.logger.error(f"Error starting detection for stream: {stream.id}: {e}")
        return jsonify({
            "error": f"Error starting detection: {e}",
            "stream_id": stream.id,
            "active": False,
            "status": stream.status or "unknown",
            "isDetecting": False,
            "isDetectionLoading": False,
            "detectionError": str(e)
        }), 500

@monitor_bp.route("/api/monitor/detection-status/<int:stream_id>", methods=["GET"])
def detection_status(stream_id):
    stream = Stream.query.get_or_404(stream_id)
    stream_url = get_stream_url(stream)
    is_active = (stream_url in stream_processors or stream.is_monitored) and stream.status != 'offline'
    stream_status = getattr(stream, 'status', 'unknown')
    
    response = {
        "stream_id": stream_id,
        "stream_url": stream_url,
        "active": is_active,
        "status": stream_status,
        "isDetecting": is_active,
        "isDetectionLoading": False,
        "detectionError": "Stream is offline" if stream.status == 'offline' else None
    }
    return jsonify(response)

@monitor_bp.route("/api/monitor/health", methods=["GET"])
def health_check():
    """Health check endpoint for monitor app."""
    active_streams = len(stream_processors)
    return jsonify({
        "status": "healthy",
        "service": "monitor",
        "active_streams": active_streams
    })

@monitor_bp.route('/api/monitoring/status', methods=['GET'])
def monitoring_status():
    """Get current monitoring status"""
    try:
        status = get_monitoring_status()
        if status:
            return jsonify({
                'success': True,
                'data': status
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to get monitoring status'
            }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error getting monitoring status: {e}'
        }), 500

@monitor_bp.route('/api/monitoring/restart', methods=['POST'])
def restart_monitoring():
    """Restart all monitoring"""
    try:
        success = restart_all_monitoring()
        if success:
            return jsonify({
                'success': True,
                'message': 'All monitoring restarted successfully'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to restart monitoring'
            }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error restarting monitoring: {e}'
        }), 500

@monitor_bp.route('/api/monitoring/stream/<int:stream_id>/start', methods=['POST'])
def start_stream_monitoring(stream_id):
    """Start monitoring for a specific stream"""
    try:
        stream = Stream.query.get(stream_id)
        if not stream:
            return jsonify({
                'success': False,
                'message': 'Stream not found'
            }), 404
            
        success = start_monitoring(stream)
        if success:
            return jsonify({
                'success': True,
                'message': f'Started monitoring for stream {stream_id}'
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Failed to start monitoring for stream {stream_id}'
            }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error starting monitoring: {e}'
        }), 500

@monitor_bp.route('/api/monitoring/stream/<int:stream_id>/stop', methods=['POST'])
def stop_stream_monitoring(stream_id):
    """Stop monitoring for a specific stream"""
    try:
        stream = Stream.query.get(stream_id)
        if not stream:
            return jsonify({
                'success': False,
                'message': 'Stream not found'
            }), 404
            
        stop_monitoring(stream)
        return jsonify({
            'success': True,
            'message': f'Stopped monitoring for stream {stream_id}'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error stopping monitoring: {e}'
        }), 500

@monitor_bp.route('/api/monitoring/health', methods=['GET'])
def monitoring_health():
    """Health check for monitoring system"""
    try:
        from sqlalchemy import text
        from datetime import datetime
        
        with current_app.app_context():
            # Check database connection
            db.session.execute(text('SELECT 1'))
            
            # Get basic stats
            total_streams = Stream.query.count()
            monitored_streams = Stream.query.filter_by(is_monitored=True).count()
            
            return jsonify({
                'success': True,
                'status': 'healthy',
                'data': {
                    'total_streams': total_streams,
                    'monitored_streams': monitored_streams,
                    'timestamp': datetime.now().isoformat()
                }
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'status': 'unhealthy',
            'message': str(e)
        }), 500

@monitor_bp.route('/status', methods=['GET'])
def get_status():
    """Get detailed monitoring status including detection-specific stats"""
    try:
        status = get_monitoring_status()
        if not status:
            return jsonify({'error': 'Failed to retrieve monitoring status'}), 500
        
        # Add detection-specific stats
        status['audio_cache_stats'] = get_cache_stats()
        status['chat_filter_stats'] = get_performance_stats()
        
        # System resources
        system_resources = {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
        }
        
        # Attempt to get active greenlets safely
        try:
            from gevent.greenlet import get_all
            system_resources['active_greenlets'] = len(get_all()) if get_all else 0
            logger.debug(f"Active greenlets counted: {system_resources['active_greenlets']}")
        except (ImportError, AttributeError):
            logger.warning("Unable to count active greenlets; omitting from status")
            system_resources['active_greenlets'] = None
        
        status['system_resources'] = system_resources
        
        return jsonify(status), 200
    except Exception as e:
        logger.error(f"Error retrieving status: {e}")
        return jsonify({'error': str(e)}), 500