import logging
import re
import requests
from datetime import datetime, timedelta
from models import DetectionLog, Stream, ChaturbateStream, StripchatStream
from extensions import db
from utils.notifications import emit_notification
import random
import time
from gevent.lock import Semaphore
import urllib3
from dotenv import load_dotenv
import os
import hashlib
from collections import defaultdict, deque
from difflib import SequenceMatcher

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Proxy configuration
PROXY_LIST = []
PROXY_LIST_LAST_UPDATED = None
PROXY_UPDATE_INTERVAL = 60
FAILED_PROXIES = set()
PROXY_SUCCESS_COUNT = defaultdict(int)
PROXY_FAILURE_COUNT = defaultdict(int)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# External dependencies
_sentiment_analyzer = None
ENABLE_CHAT_MONITORING = None
NEGATIVE_SENTIMENT_THRESHOLD = None

# Smart filtering system
class SmartChatFilter:
    def __init__(self, similarity_threshold=0.8, time_window_minutes=5, max_alerts_per_keyword=3):
        self.similarity_threshold = similarity_threshold
        self.time_window = timedelta(minutes=time_window_minutes)
        self.max_alerts_per_keyword = max_alerts_per_keyword
        
        self.recent_alerts = defaultdict(lambda: defaultdict(deque))
        self.message_hashes = defaultdict(set)
        
    def _hash_message(self, message, username):
        """Create a hash of the message content and user for exact duplicate detection"""
        content = f"{username}:{message}".lower().strip()
        return hashlib.md5(content.encode()).hexdigest()
    
    def _normalize_message(self, message):
        """Normalize message for similarity comparison"""
        normalized = re.sub(r'\s+', ' ', message.lower().strip())
        normalized = re.sub(r'[0-9]+', '#', normalized)
        normalized = re.sub(r'[!@#$%^&*()_+=\[\]{}|;:,.<>?]+', '', normalized)
        return normalized
    
    def _calculate_similarity(self, msg1, msg2):
        """Calculate similarity between two messages"""
        norm1 = self._normalize_message(msg1)
        norm2 = self._normalize_message(msg2)
        return SequenceMatcher(None, norm1, norm2).ratio()
    
    def _is_similar_to_recent(self, room_url, alert_type, new_message, new_username):
        """Check if the new alert is similar to recent ones"""
        recent = self.recent_alerts[room_url][alert_type]
        
        for alert in recent:
            if self._calculate_similarity(new_message, alert['message']) >= self.similarity_threshold:
                if alert_type == 'keyword':
                    if new_username == alert['username']:
                        return True
                elif alert_type == 'sentiment':
                    if new_username == alert['username']:
                        return True
        return False
    
    def _cleanup_old_alerts(self, room_url):
        """Remove alerts older than the time window"""
        current_time = datetime.now()
        
        for alert_type in list(self.recent_alerts[room_url].keys()):
            alerts = self.recent_alerts[room_url][alert_type]
            
            while alerts and (current_time - alerts[0]['timestamp']) > self.time_window:
                alerts.popleft()
            
            if not alerts:
                del self.recent_alerts[room_url][alert_type]
        
        if not self.recent_alerts[room_url]:
            del self.recent_alerts[room_url]
    
    def should_alert(self, room_url, detection):
        """Determine if an alert should be sent based on smart filtering"""
        current_time = datetime.now()
        message = detection.get('message', '')
        username = detection.get('username', 'unknown')
        alert_type = detection.get('type', 'unknown')
        
        self._cleanup_old_alerts(room_url)
        
        message_hash = self._hash_message(message, username)
        if message_hash in self.message_hashes[room_url]:
            logger.debug(f"Exact duplicate message detected for {room_url}: {username} - {message[:50]}...")
            return False
        
        if self._is_similar_to_recent(room_url, alert_type, message, username):
            logger.debug(f"Similar message detected for {room_url}: {username} - {message[:50]}...")
            return False
        
        recent_count = len(self.recent_alerts[room_url][alert_type])
        if recent_count >= self.max_alerts_per_keyword:
            logger.debug(f"Rate limit reached for {alert_type} alerts in {room_url}")
            return False
        
        if alert_type == 'keyword':
            keyword = detection.get('keyword', '')
            for alert in self.recent_alerts[room_url][alert_type]:
                if (alert.get('keyword') == keyword and 
                    alert.get('username') == username and
                    (current_time - alert['timestamp']).total_seconds() < 300):
                    logger.debug(f"Same keyword '{keyword}' from same user '{username}' within 5 minutes")
                    return False
        
        elif alert_type == 'sentiment':
            sentiment_score = detection.get('sentiment_score', 0)
            for alert in self.recent_alerts[room_url][alert_type]:
                if (alert.get('username') == username and
                    abs(alert.get('sentiment_score', 0) - sentiment_score) < 0.1 and
                    (current_time - alert['timestamp']).total_seconds() < 600):
                    logger.debug(f"Similar sentiment score from same user '{username}' within 10 minutes")
                    return False
        
        alert_data = {
            'message': message,
            'username': username,
            'timestamp': current_time,
            'keyword': detection.get('keyword'),
            'sentiment_score': detection.get('sentiment_score')
        }
        
        self.recent_alerts[room_url][alert_type].append(alert_data)
        self.message_hashes[room_url].add(message_hash)
        
        if len(self.message_hashes[room_url]) > 1000:
            old_hashes = list(self.message_hashes[room_url])[:100]
            for hash_to_remove in old_hashes:
                self.message_hashes[room_url].discard(hash_to_remove)
        
        return True
    
    def get_stats(self, room_url=None):
        """Get filtering statistics"""
        if room_url:
            return {
                'room_url': room_url,
                'active_alert_types': list(self.recent_alerts[room_url].keys()),
                'total_recent_alerts': sum(len(alerts) for alerts in self.recent_alerts[room_url].values()),
                'unique_message_hashes': len(self.message_hashes[room_url])
            }
        else:
            return {
                'total_rooms_tracked': len(self.recent_alerts),
                'total_recent_alerts': sum(
                    sum(len(alerts) for alerts in room_alerts.values()) 
                    for room_alerts in self.recent_alerts.values()
                ),
                'total_message_hashes': sum(len(hashes) for hashes in self.message_hashes.values())
            }

# Global smart filter instance
smart_filter = SmartChatFilter(
    similarity_threshold=0.8,
    time_window_minutes=5,
    max_alerts_per_keyword=3
)

def initialize_chat_globals(sentiment_analyzer=None, enable_chat_monitoring=None, negative_sentiment_threshold=None):
    """Initialize global variables from environment"""
    global _sentiment_analyzer, ENABLE_CHAT_MONITORING, NEGATIVE_SENTIMENT_THRESHOLD
    ENABLE_CHAT_MONITORING = enable_chat_monitoring if enable_chat_monitoring is not None else os.getenv('ENABLE_CHAT_MONITORING', 'true').lower() == 'true'
    
    if negative_sentiment_threshold is not None:
        NEGATIVE_SENTIMENT_THRESHOLD = float(negative_sentiment_threshold)
    else:
        threshold_env = os.getenv('NEGATIVE_SENTIMENT_THRESHOLD', '-0.5')
        try:
            NEGATIVE_SENTIMENT_THRESHOLD = float(threshold_env)
        except (ValueError, TypeError):
            NEGATIVE_SENTIMENT_THRESHOLD = -0.5
            logger.warning(f"Invalid NEGATIVE_SENTIMENT_THRESHOLD value '{threshold_env}', using default -0.5")
    
    _sentiment_analyzer = sentiment_analyzer if sentiment_analyzer is not None else load_sentiment_analyzer()

def load_sentiment_analyzer():
    """Load the VADER sentiment analyzer"""
    global _sentiment_analyzer
    if _sentiment_analyzer is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _sentiment_analyzer = SentimentIntensityAnalyzer()
    return _sentiment_analyzer

def update_proxy_list():
    """Fetch fresh proxies from free API services"""
    global PROXY_LIST, PROXY_LIST_LAST_UPDATED, FAILED_PROXIES
    try:
        response = requests.get(
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
            timeout=20
        )
        if response.status_code == 200 and response.text:
            proxies = [proxy.strip() for proxy in response.text.split('\n') if proxy.strip()]
            if len(proxies) > 200:
                PROXY_LIST = proxies
                PROXY_LIST_LAST_UPDATED = time.time()
                FAILED_PROXIES.clear()
                logger.info(f"Updated proxy list with {len(PROXY_LIST)} proxies")
                return proxies
                
        response = requests.get(
            "https://www.proxy-list.download/api/v1/get?type=http",
            timeout=20
        )
        if response.status_code == 200 and response.text:
            proxies = [proxy.strip() for proxy in response.text.split('\n') if proxy.strip()]
            if len(proxies) > 200:
                PROXY_LIST = proxies
                PROXY_LIST_LAST_UPDATED = time.time()
                FAILED_PROXIES.clear()
                logger.info(f"Updated proxy list with {len(PROXY_LIST)} proxies")
                return proxies
                
        logger.warning("No valid proxies retrieved from API services")
        return None
    except Exception as e:
        logger.error(f"Failed to update proxy list: {str(e)}")
        return None

def get_random_proxy(used_proxies=None):
    """Select a random proxy from the proxy list, avoiding recently failed ones."""
    global PROXY_LIST, PROXY_LIST_LAST_UPDATED, FAILED_PROXIES
    
    if used_proxies is None:
        used_proxies = set()
    
    current_time = time.time()
    if not PROXY_LIST or not PROXY_LIST_LAST_UPDATED or \
       current_time - PROXY_LIST_LAST_UPDATED > PROXY_UPDATE_INTERVAL:
        new_proxies = update_proxy_list()
        
        if new_proxies and len(new_proxies) >= 100:
            PROXY_LIST = new_proxies
            PROXY_LIST_LAST_UPDATED = current_time
            logger.info(f"Updated proxy list with {len(PROXY_LIST)} proxies")
        elif not PROXY_LIST:
            PROXY_LIST = [
                "52.67.10.183:80",
                "200.250.131.218:80",
            ]
            logger.warning("Using static proxy list as fallback")
    
    if PROXY_LIST:
        available_proxies = [p for p in PROXY_LIST if p not in FAILED_PROXIES and p not in used_proxies]
        if not available_proxies:
            logger.warning("No available proxies after filtering failed/used ones, clearing failed proxies")
            FAILED_PROXIES.clear()
            available_proxies = [p for p in PROXY_LIST if p not in used_proxies]
        
        if available_proxies:
            proxy_scores = [(p, PROXY_SUCCESS_COUNT[p] - PROXY_FAILURE_COUNT[p]) for p in available_proxies]
            proxy_scores.sort(key=lambda x: x[1], reverse=True)
            selected_proxy = proxy_scores[0][0] if proxy_scores else random.choice(available_proxies)
            logger.debug(f"Selected proxy {selected_proxy} with score {PROXY_SUCCESS_COUNT[selected_proxy] - PROXY_FAILURE_COUNT[selected_proxy]}")
            return {
                "http": f"http://{selected_proxy}",
                "https": f"http://{selected_proxy}"
            }, selected_proxy
    logger.error("No proxies available")
    return None, None

def refresh_flagged_keywords(app):
    """Retrieve current flagged keywords from database"""
    with app.app_context():
        from models import ChatKeyword
        keywords = [kw.keyword.lower() for kw in ChatKeyword.query.all()]
    logger.debug(f"Retrieved {len(keywords)} flagged keywords")
    return keywords

def get_stream_info(room_url, app):
    """Identify platform, streamer, and broadcaster UID from URL, prioritizing room_url"""
    with app.app_context():
        stream = Stream.query.filter_by(room_url=room_url).first()
        if stream:
            logger.debug(f"Found stream by room_url: {room_url}, type: {stream.type}, username: {stream.streamer_username}")
            if stream.type.lower() == 'chaturbate':
                cb_stream = ChaturbateStream.query.filter_by(id=stream.id).first()
                broadcaster_uid = cb_stream.broadcaster_uid if cb_stream else None
                return stream.type.lower(), stream.streamer_username, broadcaster_uid
            return stream.type.lower(), stream.streamer_username, None
        
        cb_stream = ChaturbateStream.query.filter_by(chaturbate_m3u8_url=room_url).first()
        if cb_stream:
            stream = Stream.query.get(cb_stream.id)
            logger.debug(f"Found stream by chaturbate_m3u8_url: {room_url}, type: chaturbate, username: {stream.streamer_username}")
            return 'chaturbate', stream.streamer_username, cb_stream.broadcaster_uid
        
        sc_stream = StripchatStream.query.filter_by(stripchat_m3u8_url=room_url).first()
        if sc_stream:
            stream = Stream.query.get(sc_stream.id)
            logger.debug(f"Found stream by stripchat_m3u8_url: {room_url}, type: stripchat, username: {stream.streamer_username}")
            return 'stripchat', stream.streamer_username, None
        
        logger.warning(f"No stream found for URL: {room_url}")
        return 'unknown', 'unknown', None

def get_stream_assignment(room_url, app):
    """Get assignment info for a stream"""
    from sqlalchemy.orm import joinedload
    with app.app_context():
        stream = Stream.query.filter_by(room_url=room_url).first()
        if not stream:
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
        
        from models import Assignment
        query = Assignment.query.options(
            joinedload(Assignment.agent),
            joinedload(Assignment.stream)
        ).filter_by(stream_id=stream.id)
        
        assignments = query.all()
        
        if not assignments:
            logger.info(f"No assignments found for stream: {room_url}")
            return None, None
        
        assignment = assignments[0]
        agent_id = assignment.agent_id
        return assignment.id, agent_id

def fetch_chaturbate_room_uid(streamer_username):
    """Fetch Chaturbate room UID and broadcaster UID"""
    url = f"https://chaturbate.com/api/chatvideocontext/{streamer_username}/"
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Referer': f'https://chaturbate.com/{streamer_username}/',
        'Connection': 'keep-alive',
    }
    max_attempts = 30
    attempts = 0
    while attempts < max_attempts:
        proxy_dict, selected_proxy = get_random_proxy()
        try:
            response = requests.get(
                url,
                headers=headers,
                proxies=proxy_dict,
                timeout=10,
                verify=False
            )
            response.raise_for_status()
            data = response.json()
            broadcaster_uid = data.get('broadcaster_uid')
            room_uid = data.get('room_uid')
            logger.debug(f"Fetched Chaturbate UIDs for {streamer_username}: broadcaster_uid={broadcaster_uid}, room_uid={room_uid}")
            return broadcaster_uid, room_uid
        except Exception as e:
            attempts += 1
            logger.warning(f"Attempt {attempts} failed for Chaturbate room UID fetch for {streamer_username}: {e}")
            if attempts < max_attempts:
                time.sleep(1)
    logger.error(f"Failed to fetch Chaturbate room UID for {streamer_username} after {max_attempts} attempts")
    return None, None

def fetch_chaturbate_chat(room_url, streamer, broadcaster_uid):
    """Fetch Chaturbate chat messages using proxies"""
    if not broadcaster_uid:
        logger.warning(f"No broadcaster UID for {room_url}")
        return []
    url = "https://chaturbate.com/push_service/room_history/"
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Referer': f'https://chaturbate.com/{streamer}/',
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'multipart/form-data; boundary=----geckoformboundary428c342290b0a9092e9dcf7e4e1d5b9',
        'Origin': 'https://chaturbate.com',
        'Connection': 'keep-alive',
    }
    data = (
        '------geckoformboundary428c342290b0a9092e9dcf7e4e1d5b9\r\n'
        f'Content-Disposition: form-data; name="topics"\r\n\r\n'
        f'{{"RoomMessageTopic#RoomMessageTopic:{broadcaster_uid}":{{"broadcaster_uid":"{broadcaster_uid}"}}}}\r\n'
        '------geckoformboundary428c342290b0a9092e9dcf7e4e1d5b9\r\n'
        'Content-Disposition: form-data; name="csrfmiddlewaretoken"\r\n\r\n'
        'NdFODN04i4jCUKVTPs3JyAwxsVnuxiy0\r\n'
        '------geckoformboundary428c342290b0a9092e9dcf7e4e1d5b9--\r\n'
    )
    max_attempts = 30
    attempts = 0
    used_proxies = set()
    
    while attempts < max_attempts:
        proxy_dict, selected_proxy = get_random_proxy(used_proxies)
        if not proxy_dict:
            logger.error(f"No proxy available for attempt {attempts + 1}")
            attempts += 1
            continue
        
        used_proxies.add(selected_proxy)
        try:
            response = requests.post(
                url,
                headers=headers,
                data=data,
                proxies=proxy_dict,
                timeout=15,
                verify=False
            )
            response.raise_for_status()
            PROXY_SUCCESS_COUNT[selected_proxy] += 1
            chat_data = response.json()
            messages = []
            for key, msg_data in chat_data.items():
                if f"RoomMessageTopic#RoomMessageTopic:{broadcaster_uid}" in msg_data:
                    msg = msg_data[f"RoomMessageTopic#RoomMessageTopic:{broadcaster_uid}"]
                    messages.append({
                        "username": msg.get("from_user", {}).get("username", "unknown"),
                        "message": msg.get("message", ""),
                        "timestamp": datetime.now().isoformat()
                    })
            logger.info(f"Fetched {len(messages)} Chaturbate chat messages for {streamer} at {room_url} using proxy {selected_proxy}")
            return messages
        except Exception as e:
            attempts += 1
            PROXY_FAILURE_COUNT[selected_proxy] += 1
            FAILED_PROXIES.add(selected_proxy)
            logger.warning(f"Attempt {attempts} failed for Chaturbate chat fetch for {streamer} at {room_url} with proxy {selected_proxy}: {e}")
            if attempts < max_attempts:
                time.sleep(1)
            if attempts % 5 == 0 and attempts < max_attempts:
                logger.info("Too many proxy failures, forcing proxy list refresh")
                update_proxy_list()
                used_proxies.clear()
    logger.error(f"Failed to fetch Chaturbate chat for {streamer} at {room_url} after {max_attempts} attempts")
    return []

def fetch_stripchat_chat(room_url, streamer):
    """Fetch Stripchat chat messages using proxies"""
    url = f"https://stripchat.com/api/front/v2/models/username/{streamer}/chat"
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Referer': f'https://stripchat.com/{streamer}',
        'content-type': 'application/json',
        'front-version': '11.1.89',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'Connection': 'keep-alive',
    }
    max_attempts = 30
    attempts = 0
    used_proxies = set()
    
    while attempts < max_attempts:
        proxy_dict, selected_proxy = get_random_proxy(used_proxies)
        if not proxy_dict:
            logger.error(f"No proxy available for attempt {attempts + 1}")
            attempts += 1
            continue
        
        used_proxies.add(selected_proxy)
        try:
            response = requests.get(
                url,
                headers=headers,
                proxies=proxy_dict,
                timeout=15,
                verify=False
            )
            response.raise_for_status()
            PROXY_SUCCESS_COUNT[selected_proxy] += 1
            chat_data = response.json().get("messages", [])
            messages = []
            for msg in chat_data:
                message_type = msg.get("type", "")
                details = msg.get("details", {})
                body = details.get("body", "")
                if message_type == "text" or (message_type == "tip" and body):
                    messages.append({
                        "username": msg.get("userData", {}).get("username", "unknown"),
                        "message": body,
                        "timestamp": msg.get("createdAt", datetime.now().isoformat())
                    })
            logger.info(f"Fetched {len(messages)} Stripchat chat messages for {streamer} at {room_url} using proxy {selected_proxy}")
            return messages
        except Exception as e:
            attempts += 1
            PROXY_FAILURE_COUNT[selected_proxy] += 1
            FAILED_PROXIES.add(selected_proxy)
            logger.warning(f"Attempt {attempts} failed for Stripchat chat fetch for {streamer} at {room_url} with proxy {selected_proxy}: {e}")
            if attempts < max_attempts:
                time.sleep(1)
            if attempts % 5 == 0 and attempts < max_attempts:
                logger.info("Too many proxy failures, forcing proxy list refresh")
                update_proxy_list()
                used_proxies.clear()
    logger.error(f"Failed to fetch Stripchat chat for {streamer} at {room_url} after {max_attempts} attempts")
    return []

# chat_processing.py
def fetch_chat_messages(room_url, app):
    """Fetch chat messages based on platform"""
    with app.app_context():  # Ensure context
        logger.debug(f"Fetching chat messages for room_url: {room_url}")
        try:
            platform, streamer, broadcaster_uid = get_stream_info(room_url, app)
            logger.debug(f"Platform: {platform}, Streamer: {streamer}, Broadcaster UID: {broadcaster_uid}")
            if platform == "chaturbate":
                return fetch_chaturbate_chat(room_url, streamer, broadcaster_uid)
            elif platform == "stripchat":
                return fetch_stripchat_chat(room_url, streamer)
            else:
                logger.warning(f"Unsupported platform {platform} for {room_url}")
                return []
        except Exception as e:
            logger.error(f"Chat fetch error for {room_url}: {e}")
            return []

def process_chat_messages(messages, room_url, app):
    """Analyze chat messages for keywords and sentiment with smart filtering"""
    global NEGATIVE_SENTIMENT_THRESHOLD
    if NEGATIVE_SENTIMENT_THRESHOLD is None:
        logger.warning("NEGATIVE_SENTIMENT_THRESHOLD is None, initializing with default value")
        NEGATIVE_SENTIMENT_THRESHOLD = -0.5
    
    try:
        keywords = refresh_flagged_keywords(app)
        if not keywords:
            logger.debug(f"No flagged keywords found for {room_url}")
            return []
        
        detected = []
        now = datetime.now()
        analyzer = load_sentiment_analyzer()
        
        for msg in messages:
            text = msg.get("message", "").lower()
            user = msg.get("username", "unknown")
            timestamp = msg.get("timestamp", now.isoformat())
            
            for keyword in keywords:
                if keyword in text:
                    detection = {
                        "type": "keyword",
                        "keyword": keyword,
                        "message": text,
                        "username": user,
                        "timestamp": timestamp
                    }
                    
                    if smart_filter.should_alert(room_url, detection):
                        detected.append(detection)
                        logger.info(f"Keyword alert passed smart filter: {keyword} from {user}")
                    else:
                        logger.debug(f"Keyword alert filtered out: {keyword} from {user}")
            
            try:
                sentiment = analyzer.polarity_scores(text)
                compound_score = sentiment.get('compound')
                
                if compound_score is not None and NEGATIVE_SENTIMENT_THRESHOLD is not None and compound_score < NEGATIVE_SENTIMENT_THRESHOLD:
                    detection = {
                        "type": "sentiment",
                        "sentiment_score": compound_score,
                        "message": text,
                        "username": user,
                        "timestamp": timestamp
                    }
                    
                    if smart_filter.should_alert(room_url, detection):
                        detected.append(detection)
                        logger.info(f"Negative sentiment alert passed smart filter: score {compound_score} from {user}")
                    else:
                        logger.debug(f"Negative sentiment alert filtered out: score {compound_score} from {user}")
            except Exception as e:
                logger.error(f"Error analyzing sentiment for message from {user}: {e}")
        
        return detected
    
    except Exception as e:
        logger.error(f"Error processing chat messages for {room_url}: {e}")
        return []

def log_chat_detection(detections, room_url, app):
    """Log chat detections to database"""
    with app.app_context():
        if not os.getenv('ENABLE_CHAT_MONITORING', 'true').lower() == 'true':
            logger.info(f"Chat monitoring disabled for {room_url}")
            return
        
        try:
            platform, streamer, _ = get_stream_info(room_url, app)
            assignment_id, agent_id = get_stream_assignment(room_url, app)
            
            for detection in detections:
                details = {
                    "type": detection.get("type"),
                    "keyword": detection.get("keyword"),
                    "sentiment_score": detection.get("sentiment_score"),
                    "message": detection.get("message"),
                    "username": detection.get("username"),
                    "timestamp": detection.get("timestamp"),
                    "streamer_name": streamer,
                    "platform": platform,
                    "assigned_agent": agent_id
                }
                
                log_entry = DetectionLog(
                    room_url=room_url,
                    event_type=f"chat_{detection.get('type')}_detection",
                    details=details,
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
            logger.error(f"Error logging chat detection for {room_url}: {e}")
            db.session.rollback()

def get_filtering_stats(room_url=None):
    """Get smart filtering statistics"""
    return smart_filter.get_stats(room_url)

def configure_smart_filter(similarity_threshold=None, time_window_minutes=None, max_alerts_per_keyword=None):
    """Configure smart filter parameters"""
    global smart_filter
    
    if similarity_threshold is not None:
        smart_filter.similarity_threshold = similarity_threshold
        logger.info(f"Updated smart filter similarity threshold to {similarity_threshold}")
    if time_window_minutes is not None:
        smart_filter.time_window = timedelta(minutes=time_window_minutes)
        logger.info(f"Updated smart filter time window to {time_window_minutes} minutes")
    if max_alerts_per_keyword is not None:
        smart_filter.max_alerts_per_keyword = max_alerts_per_keyword
        logger.info(f"Updated smart filter max alerts per keyword to {max_alerts_per_keyword}")

def reset_smart_filter(room_url=None):
    """Reset smart filter data for a specific room or all rooms"""
    global smart_filter
    
    if room_url:
        if room_url in smart_filter.recent_alerts:
            del smart_filter.recent_alerts[room_url]
        if room_url in smart_filter.message_hashes:
            del smart_filter.message_hashes[room_url]
        logger.info(f"Reset smart filter data for room: {room_url}")
    else:
        smart_filter.recent_alerts.clear()
        smart_filter.message_hashes.clear()
        logger.info("Reset all smart filter data")

def get_duplicate_message_analysis(room_url, hours_back=1, app=None):
    """Analyze potential duplicate messages in recent logs for debugging"""
    if app is None:
        raise ValueError("Flask app instance is required for database access")
    
    with app.app_context():
        cutoff_time = datetime.now() - timedelta(hours=hours_back)
        logs = DetectionLog.query.filter(
            DetectionLog.room_url == room_url,
            DetectionLog.timestamp >= cutoff_time,
            DetectionLog.event_type.in_(['chat_detection', 'chat_sentiment_detection'])
        ).order_by(DetectionLog.timestamp.desc()).all()
        
        messages = []
        for log in logs:
            detection = log.details.get('detection', {})
            messages.append({
                'id': log.id,
                'message': detection.get('message', ''),
                'username': detection.get('username', ''),
                'type': detection.get('type', ''),
                'keyword': detection.get('keyword'),
                'sentiment_score': detection.get('sentiment_score'),
                'timestamp': log.timestamp
            })
        
        # Group similar messages
        similar_groups = []
        processed = set()
        
        for i, msg1 in enumerate(messages):
            if i in processed:
                continue
                
            group = [msg1]
            processed.add(i)
            
            for j, msg2 in enumerate(messages[i+1:], i+1):
                if j in processed:
                    continue
                    
                if smart_filter._calculate_similarity(msg1['message'], msg2['message']) >= 0.7:
                    group.append(msg2)
                    processed.add(j)
            
            if len(group) > 1:
                similar_groups.append(group)
        
        return {
            'room_url': room_url,
            'analysis_period_hours': hours_back,
            'total_messages': len(messages),
            'similar_groups': len(similar_groups),
            'potentially_duplicate_messages': sum(len(group) for group in similar_groups),
            'groups': similar_groups[:5]  # Return first 5 groups for inspection
        }

def export_filter_config():
    """Export current filter configuration"""
    return {
        'similarity_threshold': smart_filter.similarity_threshold,
        'time_window_minutes': smart_filter.time_window.total_seconds() / 60,
        'max_alerts_per_keyword': smart_filter.max_alerts_per_keyword,
        'current_stats': smart_filter.get_stats()
    }

def import_filter_config(config):
    """Import filter configuration"""
    configure_smart_filter(
        similarity_threshold=config.get('similarity_threshold'),
        time_window_minutes=config.get('time_window_minutes'),
        max_alerts_per_keyword=config.get('max_alerts_per_keyword')
    )
    logger.info(f"Imported smart filter configuration: {config}")

# Performance monitoring
class FilterPerformanceMonitor:
    def __init__(self):
        self.filter_calls = 0
        self.alerts_allowed = 0
        self.alerts_blocked = 0
        self.start_time = datetime.now()
    
    def record_filter_call(self, allowed):
        self.filter_calls += 1
        if allowed:
            self.alerts_allowed += 1
        else:
            self.alerts_blocked += 1
    
    def get_stats(self):
        runtime = (datetime.now() - self.start_time).total_seconds()
        return {
            'runtime_seconds': runtime,
            'total_filter_calls': self.filter_calls,
            'alerts_allowed': self.alerts_allowed,
            'alerts_blocked': self.alerts_blocked,
            'block_rate': self.alerts_blocked / max(self.filter_calls, 1),
            'calls_per_second': self.filter_calls / max(runtime, 1)
        }
    
    def reset(self):
        self.filter_calls = 0
        self.alerts_allowed = 0
        self.alerts_blocked = 0
        self.start_time = datetime.now()

# Global performance monitor
performance_monitor = FilterPerformanceMonitor()

# Override the should_alert method to include performance monitoring
original_should_alert = smart_filter.should_alert

def monitored_should_alert(room_url, detection):
    result = original_should_alert(room_url, detection)
    performance_monitor.record_filter_call(result)
    return result

smart_filter.should_alert = monitored_should_alert

def get_performance_stats():
    """Get performance statistics for the smart filter"""
    return performance_monitor.get_stats()

def reset_performance_stats():
    """Reset performance monitoring statistics"""
    performance_monitor.reset()
    logger.info("Reset smart filter performance statistics")