from flask import Blueprint, jsonify
from extensions import redis_service
import logging

logger = logging.getLogger(__name__)

cache_bp = Blueprint('cache', __name__, url_prefix='/api')

@cache_bp.route('/refresh-cache', methods=['POST'])
def refresh_cache():
    try:
        if redis_service.is_available():
            # Clear all relevant Redis cache keys
            keys = redis_service.redis_client.keys("stream:status:*")
            keys.extend(redis_service.redis_client.keys("dashboard:stats"))
            keys.extend(redis_service.redis_client.keys("session:user:*"))
            keys.extend(redis_service.redis_client.keys("active:user:*"))
            keys.extend(redis_service.redis_client.keys("cooldown:*"))
            
            if keys:
                redis_service.redis_client.delete(*keys)
                logger.info(f"Cleared {len(keys)} cache keys")
            else:
                logger.info("No cache keys found to clear")
                
            return jsonify({"message": "Cache cleared successfully"}), 200
        else:
            logger.warning("Redis not available, cannot clear cache")
            return jsonify({"error": "Cache service unavailable"}), 503
    except Exception as e:
        logger.error(f"Cache clear failed: {str(e)}")
        return jsonify({"error": "Failed to clear cache"}), 500