from flask import Blueprint, jsonify
from services.communication_service import communication_service
import time

health_bp = Blueprint('health', __name__)

@health_bp.route("/api/health", methods=["GET"])
def health_check():
    """Health check endpoint for main app."""
    return jsonify({
        "status": "healthy",
        "service": "main",
        "timestamp": time.time()
    })

@health_bp.route("/api/health/monitor", methods=["GET"])
def check_monitor_health():
    """Check if monitor service is accessible."""
    try:
        response = communication_service.get_from_monitor("/api/monitor/health")
        if "error" not in response:
            return jsonify({
                "monitor_status": "healthy",
                "communication": "ok",
                "monitor_data": response
            })
        else:
            return jsonify({
                "monitor_status": "unhealthy",
                "communication": "failed",
                "error": response.get("error")
            }), 503
    except Exception as e:
        return jsonify({
            "monitor_status": "unreachable",
            "communication": "failed",
            "error": str(e)
        }), 503