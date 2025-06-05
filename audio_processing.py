# audio_processing.py
import logging
import numpy as np
from datetime import datetime, timedelta
import os
import librosa
import hashlib
from flask import current_app
from gevent.lock import Semaphore
from models import DetectionLog, Stream, ChaturbateStream, StripchatStream, ChatKeyword
from extensions import db
from utils.notifications import emit_notification
from dotenv import load_dotenv
from collections import defaultdict
import timeout_decorator

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# External dependencies
_whisper_model = None

# Smart alert filtering system
_alert_cache = defaultdict(dict)

# Configuration for smart filtering
DUPLICATE_WINDOW_MINUTES = int(os.getenv('DUPLICATE_ALERT_WINDOW_MINUTES', 5))
SIMILARITY_THRESHOLD = float(os.getenv('TRANSCRIPT_SIMILARITY_THRESHOLD', 0.8))
MAX_CACHE_SIZE = int(os.getenv('MAX_ALERT_CACHE_SIZE', 10000))
TRANSCRIPTION_TIMEOUT = int(os.getenv('TRANSCRIPTION_TIMEOUT', 900))

def initialize_audio_globals(whisper_model=None):
    """Initialize global variables for model"""
    global _whisper_model
    _whisper_model = whisper_model
    logger.info("Audio globals initialized")

def load_whisper_model(app=None):
    """Load the OpenAI Whisper model with configurable size and fallback"""
    if app is None:
        app = current_app._get_current_object()

    with app.app_context():
        enable_audio_monitoring = os.getenv('ENABLE_AUDIO_MONITORING', 'true').lower() == 'true'
        if not enable_audio_monitoring:
            logger.info("Audio monitoring disabled; skipping Whisper model loading")
            return None
        global _whisper_model
        if _whisper_model is None:
            try:
                import whisper
                model_size = os.getenv('WHISPER_MODEL_SIZE', 'tiny')
                logger.info(f"Loading Whisper model: {model_size}")
                _whisper_model = whisper.load_model(model_size)  # Force CPU to avoid GPU contention
                logger.info(f"Whisper model '{model_size}' loaded successfully")
            except AttributeError as e:
                logger.error(f"Whisper attribute error: {e}. Ensure 'openai-whisper' is installed correctly.")
                try:
                    logger.info("Attempting to load fallback 'tiny' model")
                    _whisper_model = whisper.load_model("tiny", device="cpu")
                    logger.info("Fallback Whisper model loaded")
                except Exception as e2:
                    logger.error(f"Error loading fallback model: {e2}")
                    _whisper_model = None
            except Exception as e:
                logger.error(f"Error loading Whisper model: {e}")
                _whisper_model = None
        if _whisper_model is None:
            logger.warning("Whisper model unavailable; audio processing will be skipped.")
        return _whisper_model

def refresh_flagged_keywords(app=None):
    """Retrieve current flagged keywords from database"""
    if app is None:
        app = current_app._get_current_object()

    with app.app_context():
        try:
            keywords = [kw.keyword.lower() for kw in ChatKeyword.query.all()]
            logger.debug(f"Retrieved {len(keywords)} flagged keywords: {keywords}")
            return keywords
        except Exception as e:
            logger.error(f"Error retrieving flagged keywords: {e}")
            return []

def get_stream_info(stream_url, app=None):
    """Identify platform and streamer from URL"""
    if app is None:
        app = current_app._get_current_object()

    with app.app_context():
        try:
            cb_stream = ChaturbateStream.query.filter_by(chaturbate_m3u8_url=stream_url).first()
            if cb_stream:
                return 'chaturbate', cb_stream.streamer_username
            sc_stream = StripchatStream.query.filter_by(stripchat_m3u8_url=stream_url).first()
            if sc_stream:
                return 'stripchat', sc_stream.streamer_username
            stream = Stream.query.filter_by(room_url=stream_url).first()
            if stream:
                return stream.type.lower(), stream.streamer_username
            logger.warning(f"No stream found for URL: {stream_url}")
            return 'unknown', 'unknown'
        except Exception as e:
            logger.error(f"Error getting stream info for {stream_url}: {e}")
            return 'unknown', 'unknown'

def get_stream_assignment(stream_url, app=None):
    """Get assignment info for a stream"""
    if app is None:
        app = current_app._get_current_object()

    from sqlalchemy.orm import joinedload
    with app.app_context():
        try:
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
            logger.error(f"Error getting stream assignment for {stream_url}: {e}")
            return None, None

def normalize_audio(audio_data):
    """Normalize audio volume to improve transcription reliability"""
    try:
        max_amplitude = np.max(np.abs(audio_data))
        if max_amplitude > 0:
            audio_data = audio_data / max_amplitude
        return audio_data
    except Exception as e:
        logger.error(f"Error normalizing audio: {e}")
        return audio_data

def calculate_text_similarity(text1, text2):
    """Calculate similarity between two text strings using simple word overlap"""
    if not text1 or not text2:
        return 0.0
    
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    
    return len(intersection) / len(union) if union else 0.0

def generate_alert_hash(keywords, transcript, stream_url):
    """Generate a hash for alert deduplication based on keywords and partial transcript"""
    content = f"{stream_url}:{sorted(keywords)}:{transcript[:50].lower().strip()}"
    return hashlib.md5(content.encode()).hexdigest()

def cleanup_old_cache_entries(stream_url):
    """Remove old cache entries to prevent memory bloat"""
    global _alert_cache
    
    if stream_url not in _alert_cache:
        return
    
    current_time = datetime.now()
    cutoff_time = current_time - timedelta(minutes=DUPLICATE_WINDOW_MINUTES)
    
    keys_to_remove = []
    for alert_hash, alert_data in _alert_cache[stream_url].items():
        if alert_data['timestamp'] < cutoff_time:
            keys_to_remove.append(alert_hash)
    
    for key in keys_to_remove:
        del _alert_cache[stream_url][key]
    
    if len(_alert_cache[stream_url]) > MAX_CACHE_SIZE:
        sorted_entries = sorted(
            _alert_cache[stream_url].items(),
            key=lambda x: x[1]['timestamp'],
            reverse=True
        )
        _alert_cache[stream_url] = dict(sorted_entries[:MAX_CACHE_SIZE])

def is_duplicate_alert(detection, stream_url):
    """Check if this alert is a duplicate of a recent one"""
    global _alert_cache
    
    cleanup_old_cache_entries(stream_url)
    
    keywords = detection.get("keyword", [])
    transcript = detection.get("transcript", "")
    current_time = datetime.now()
    
    alert_hash = generate_alert_hash(keywords, transcript, stream_url)
    
    if alert_hash in _alert_cache[stream_url]:
        logger.info(f"Duplicate alert detected (exact match) for {stream_url}: {keywords}")
        return True
    
    for cached_hash, cached_data in _alert_cache[stream_url].items():
        cached_keywords = set(cached_data['keywords'])
        current_keywords = set(keywords)
        
        keyword_overlap = len(cached_keywords.intersection(current_keywords))
        if keyword_overlap > 0:
            similarity = calculate_text_similarity(transcript, cached_data['transcript'])
            
            if similarity >= SIMILARITY_THRESHOLD:
                logger.info(f"Similar alert detected for {stream_url}: {keywords} (similarity: {similarity:.2f})")
                return True
    
    _alert_cache[stream_url][alert_hash] = {
        'timestamp': current_time,
        'keywords': keywords,
        'transcript': transcript
    }
    
    return False

@timeout_decorator.timeout(TRANSCRIPTION_TIMEOUT, use_signals=False)
def transcribe_audio(model, audio_data):
    """Wrapper for Whisper transcription with timeout"""
    try:
        result = model.transcribe(audio_data, fp16=False, verbose=False)
        logger.debug(f"Transcription result: {result}")
        return result
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise

def process_audio_segment(audio_data, original_sample_rate, stream_url, app=None):
    """Process an audio segment for transcription and analysis with diagnostics"""
    if app is None:
        app = current_app._get_current_object()

    with app.app_context():  # Ensure context
        enable_audio_monitoring = os.getenv('ENABLE_AUDIO_MONITORING', 'true').lower() == 'true'
        if not enable_audio_monitoring:
            logger.info(f"Audio monitoring disabled for {stream_url}")
            return [], ""
        model = load_whisper_model(app)
        if model is None:
            logger.warning(f"Skipping audio processing for {stream_url} due to unavailable Whisper model")
            return [], ""
    
        audio_duration = len(audio_data) / original_sample_rate
        audio_amplitude = np.max(np.abs(audio_data)) if len(audio_data) > 0 else 0
        logger.info(f"Audio segment for {stream_url}: duration={audio_duration:.2f}s, sample_rate={original_sample_rate}, max_amplitude={audio_amplitude:.4f}")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Processing audio segment for {stream_url}: duration={audio_duration:.2f}s, amplitude={audio_amplitude:.4f}")
        
        if audio_amplitude < 1e-5:
            logger.warning(f"Audio segment for {stream_url} has very low amplitude; may be silent")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Skipping silent audio segment for {stream_url}")
            return [], ""
        
        try:
            target_sr = 16000
            audio_data = normalize_audio(audio_data)
            if original_sample_rate != target_sr:
                logger.debug(f"Resampling audio for {stream_url} from {original_sample_rate} to {target_sr}")
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Resampling audio for {stream_url}")
                audio_data = librosa.resample(audio_data, orig_sr=original_sample_rate, target_sr=target_sr)
            logger.info(f"Transcribing audio for {stream_url}")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting transcription for {stream_url}")
            result = transcribe_audio(model, audio_data)
            transcript = result.get("text", "").strip()
            logger.debug(f"Raw transcription result for {stream_url}: {result}")
            if transcript:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Transcript for {stream_url}: {transcript}")
                logger.info(f"Transcription for {stream_url}: {transcript[:100]}...")
            else:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] No transcript for {stream_url}: Audio may be silent or unintelligible")
                logger.warning(f"Empty transcription for {stream_url}; audio may be silent or unintelligible")
            keywords = refresh_flagged_keywords(app)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Flagged keywords for {stream_url}: {keywords}")
            detected_keywords = [kw for kw in keywords if kw in transcript.lower()]
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Detected keywords for {stream_url}: {detected_keywords}")
            detections = []
            if detected_keywords:
                detection = {
                    "timestamp": datetime.now().isoformat(),
                    "transcript": transcript,
                    "keyword": detected_keywords
                }
                
                if not is_duplicate_alert(detection, stream_url):
                    detections.append(detection)
                    logger.info(f"New unique alert for {stream_url}: {detected_keywords}")
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Audio detection for {stream_url}: {detected_keywords}")
                else:
                    logger.info(f"Skipping duplicate alert for {stream_url}: {detected_keywords}")
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Skipped duplicate audio detection for {stream_url}: {detected_keywords}")
            elif transcript:
                logger.info(f"No keywords detected in transcript for {stream_url}")
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] No keywords detected in transcript for {stream_url}")
        
            return detections, transcript
        except timeout_decorator.TimeoutError:
            logger.error(f"Transcription timed out for {stream_url} after {TRANSCRIPTION_TIMEOUT}s")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Transcription timeout for {stream_url}")
            return [], ""
        except Exception as e:
            logger.error(f"Error processing audio for {stream_url}: {e}")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error processing audio for {stream_url}: {e}")
            return [], ""

def log_audio_detection(detection, stream_url, app=None):
    """Log audio detections to database"""
    if app is None:
        app = current_app._get_current_object()

    with app.app_context():
        enable_audio_monitoring = os.getenv('ENABLE_AUDIO_MONITORING', 'true').lower() == 'true'
        if not enable_audio_monitoring:
            logger.info(f"Audio monitoring disabled; skipping logging for {stream_url}")
            return
        try:
            platform, streamer = get_stream_info(stream_url, app)
            assignment_id, agent_id = get_stream_assignment(stream_url, app)
            details = {
                "keyword": detection.get("keyword"),
                "transcript": detection.get("transcript"),
                "timestamp": detection.get("timestamp"),
                "streamer_name": streamer,
                "platform": platform,
                "assigned_agent": agent_id
            }
            log_entry = DetectionLog(
                room_url=stream_url,
                event_type="audio_detection",
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
            logger.info(f"Logged audio detection for {stream_url}: {details}")
        except Exception as e:
            logger.error(f"Error logging audio detection for {stream_url}: {e}")
            db.session.rollback()

def clear_alert_cache(stream_url=None):
    """Clear alert cache for a specific stream or all streams"""
    global _alert_cache
    
    if stream_url:
        if stream_url in _alert_cache:
            del _alert_cache[stream_url]
            logger.info(f"Cleared alert cache for stream: {stream_url}")
    else:
        _alert_cache.clear()
        logger.info("Cleared all alert caches")

def get_cache_stats():
    """Get statistics about the alert cache"""
    global _alert_cache
    
    stats = {
        "total_streams": len(_alert_cache),
        "total_cached_alerts": sum(len(cache) for cache in _alert_cache.values()),
        "streams": {}
    }
    
    for stream_url, cache in _alert_cache.items():
        stats["streams"][stream_url] = {
            "cached_alerts": len(cache),
            "oldest_alert": min((data['timestamp'] for data in cache.values()), default=None),
            "newest_alert": max((data['timestamp'] for data in cache.values()), default=None)
        }
    
    return stats

def cleanup_audio_resources(app=None):
    """Clean up audio processing resources"""
    if app is None:
        app = current_app._get_current_object()

    global _whisper_model
    try:
        if _whisper_model is not None:
            del _whisper_model
            _whisper_model = None
        logger.info("Audio processing resources cleaned up")
    except Exception as e:
        logger.error(f"Error cleaning up audio resources: {e}")