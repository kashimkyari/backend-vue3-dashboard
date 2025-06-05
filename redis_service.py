import redis
import orjson
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import os

logger = logging.getLogger(__name__)

class RedisService:
    def __init__(self, app=None):
        self.redis_client = None
        self._is_available = False
        self._last_checked = None
        self._check_interval = 60  # Increased to reduce overhead
        if app:
            self.init_app(app)

    def init_app(self, app):
        try:
            redis_host = os.getenv('REDIS_HOST', 'localhost')
            redis_port = int(os.getenv('REDIS_PORT', 6379))
            redis_password = os.getenv('REDIS_PASSWORD', None)
            redis_db = int(os.getenv('REDIS_DB', 0))
            
            self.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                db=redis_db,
                decode_responses=True,
                socket_keepalive=True,
                socket_keepalive_options={},
                health_check_interval=60,  # Increased interval
                connection_pool=redis.ConnectionPool(
                    max_connections=50,  # Increased for better concurrency
                    retry_on_timeout=True
                )
            )
            
            self.redis_client.ping()
            logger.info(f"Redis connected successfully to {redis_host}:{redis_port}")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            self.redis_client = None
    
    def is_available(self) -> bool:
        now = datetime.now()
        if self._last_checked is None or (now - self._last_checked).total_seconds() > self._check_interval:
            try:
                self._is_available = self.redis_client is not None and self.redis_client.ping()
                self._last_checked = now
            except:
                self._is_available = False
        return self._is_available
    
    def cache_set(self, key: str, value: Any, expire: int = 1800) -> bool:
        if not self.is_available():
            return False
        try:
            serialized_value = orjson.dumps(value).decode('utf-8')
            return self.redis_client.setex(key, expire, serialized_value)
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False

    def cache_get(self, key: str) -> Optional[Any]:
        if not self.is_available():
            return None
        try:
            value = self.redis_client.get(key)
            return orjson.loads(value) if value else None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None
    
    def cache_delete(self, key: str) -> bool:
        if not self.is_available():
            return False
        try:
            return bool(self.redis_client.delete(key))
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            return False
    
    def cache_exists(self, key: str) -> bool:
        if not self.is_available():
            return False
        try:
            return bool(self.redis_client.exists(key))
        except Exception as e:
            logger.error(f"Cache exists error: {e}")
            return False
    
    def set_user_session(self, user_id: int, session_data: Dict, expire: int = 86400):
        key = f"session:user:{user_id}"
        return self.cache_set(key, session_data, expire)
    
    def get_user_session(self, user_id: int) -> Optional[Dict]:
        key = f"session:user:{user_id}"
        return self.cache_get(key)
    
    def clear_user_session(self, user_id: int) -> bool:
        key = f"session:user:{user_id}"
        return self.cache_delete(key)
    
    def cache_stream_status(self, stream_id: int, status_data: Dict, expire: int = 300):
        key = f"stream:status:{stream_id}"
        return self.cache_set(key, status_data, expire)
    
    def get_stream_status(self, stream_id: int) -> Optional[Dict]:
        key = f"stream:status:{stream_id}"
        return self.cache_get(key)
    
    def publish_notification(self, channel: str, data: Dict):
        if not self.is_available():
            return False
        try:
            message = orjson.dumps(data).decode('utf-8')
            return self.redis_client.publish(channel, message)
        except Exception as e:
            logger.error(f"Publish error: {e}")
            return False
    
    def subscribe_to_notifications(self, channels: List[str]):
        if not self.is_available():
            return None
        try:
            pubsub = self.redis_client.pubsub()
            pubsub.subscribe(*channels)
            return pubsub
        except Exception as e:
            logger.error(f"Subscribe error: {e}")
            return None
    
    def check_rate_limit(self, key: str, limit: int, window: int) -> bool:
        if not self.is_available():
            return True
        try:
            with self.redis_client.pipeline() as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, window)
                current, _ = pipeline.execute()
            return current <= limit
        except Exception as e:
            logger.error(f"Rate limit error: {e}")
            return True
    
    def set_detection_cooldown(self, detection_type: str, room_url: str, cooldown_seconds: int):
        key = f"cooldown:{detection_type}:{room_url}"
        return self.cache_set(key, datetime.now().isoformat(), cooldown_seconds)
    
    def is_detection_on_cooldown(self, detection_type: str, room_url: str) -> bool:
        key = f"cooldown:{detection_type}:{room_url}"
        return self.cache_exists(key)
    
    def mark_user_active(self, user_id: int, expire: int = 300):
        key = f"active:user:{user_id}"
        return self.cache_set(key, datetime.now().isoformat(), expire)
    
    def get_active_users(self) -> List[int]:
        if not self.is_available():
            return []
        try:
            keys = self.redis_client.keys("active:user:*")
            return [int(key.split(":")[-1]) for key in keys]
        except Exception as e:
            logger.error(f"Get active users error: {e}")
            return []
    
    def cache_dashboard_stats(self, stats: Dict, expire: int = 300):
        key = "dashboard:stats"
        return self.cache_set(key, stats, expire)
    
    def get_dashboard_stats(self) -> Optional[Dict]:
        key = "dashboard:stats"
        return self.cache_get(key)
    
    def push_to_queue(self, queue_name: str, item: Any) -> bool:
        if not self.is_available():
            return False
        try:
            serialized_item = orjson.dumps(item).decode('utf-8')
            return bool(self.redis_client.lpush(queue_name, serialized_item))
        except Exception as e:
            logger.error(f"Queue push error: {e}")
            return False
    
    def pop_from_queue(self, queue_name: str, timeout: int = 0) -> Optional[Any]:
        if not self.is_available():
            return None
        try:
            if timeout > 0:
                result = self.redis_client.brpop(queue_name, timeout)
                return orjson.loads(result[1]) if result else None
            else:
                result = self.redis_client.rpop(queue_name)
                return orjson.loads(result) if result else None
        except Exception as e:
            logger.error(f"Queue pop error: {e}")
            return None
    
    def get_queue_length(self, queue_name: str) -> int:
        if not self.is_available():
            return 0
        try:
            return self.redis_client.llen(queue_name)
        except Exception as e:
            logger.error(f"Queue length error: {e}")
            return 0

redis_service = RedisService()