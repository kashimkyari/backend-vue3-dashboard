# routes/dashboard_routes.py
from flask import Blueprint, jsonify, session, current_app
from extensions import db, redis_service
from models import Stream, Assignment, User
from utils import login_required
from sqlalchemy.orm import joinedload

dashboard_bp = Blueprint('dashboard', __name__)

# --------------------------------------------------------------------
# Dashboard Endpoints
# --------------------------------------------------------------------
@dashboard_bp.route("/api/dashboard", methods=["GET"])
def get_dashboard():
    try:
        # Check if Redis is enabled and try to get cached data
        if current_app.config.get('REDIS_ENABLED') and redis_service.is_available():
            cached_data = redis_service.get_dashboard_stats()
            if cached_data:
                return jsonify(cached_data), 200

        # Fetch streams with their assignments and agents eagerly loaded
        streams = Stream.query.options(
            joinedload(Stream.assignments).joinedload(Assignment.agent)
        ).all()
        data = []
        for stream in streams:
            assignment = stream.assignments[0] if stream.assignments else None
            agent_data = None
            if assignment and assignment.agent:
                # Fetch the User object to get the username
                agent = User.query.filter_by(id=assignment.agent_id, role="agent").first()
                if agent:
                    # Serialize only the relevant agent data (username)
                    agent_data = {
                        "id": agent.id,
                        "username": agent.username,
                        "role": agent.role,
                        "online": agent.online
                    }
                else:
                    current_app.logger.warning(f"Agent with ID {assignment.agent_id} not found for stream {stream.id}")
                    agent_data = None

            stream_data = {
                **stream.serialize(),
                "agent": agent_data,  # Include username instead of just agent_id
                "confidence": 0.8
            }
            data.append(stream_data)
        
        response_data = {
            "ongoing_streams": len(data),
            "streams": data
        }

        # Cache the response data if Redis is enabled
        if current_app.config.get('REDIS_ENABLED') and redis_service.is_available():
            redis_service.cache_dashboard_stats(
                response_data,
                expire=current_app.config.get('DASHBOARD_STATS_CACHE_TIMEOUT', 1800)
            )

        return jsonify(response_data), 200
    except Exception as e:
        current_app.logger.error(f"Error in /api/dashboard: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@dashboard_bp.route("/api/agent/dashboard", methods=["GET"])
def get_agent_dashboard():
    agent_id = session["user_id"]
    assignments = Assignment.query.filter_by(agent_id=agent_id).all()
    return jsonify({
        "ongoing_streams": len(assignments),
        "assignments": [a.stream.serialize() for a in assignments if a.stream]
    })