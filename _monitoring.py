import os
import time
import logging
import numpy as np
from datetime import datetime, timedelta
import av
import json
import hashlib
import requests
from flask import current_app
from models import (
    ChatKeyword, FlaggedObject, DetectionLog, Stream, User, Assignment,
    ChaturbateStream, StripchatStream
)
from extensions import db
from utils.notifications import emit_notification, emit_stream_update
from sqlalchemy.orm import joinedload, with_polymorphic
import gevent
from gevent.pool import Pool
from gevent.event import Event
from audio_processing import process_audio_segment, log_audio_detection
from video_processing import process_video_frame, log_video_detection
from chat_processing import (
    fetch_chat_messages, process_chat_messages, log_chat_detection,
    initialize_chat_globals, load_sentiment_analyzer, fetch_chaturbate_room_uid, configure_smart_filter
)
from dotenv import load_dotenv
import psutil
import cv2
from services.notification_service import NotificationService

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Global variables
_whisper_model = None
_yolo_model = None
_sentiment_analyzer = None
last_visual_alerts = {}
last_chat_alerts = {}
stream_processors = {}  # Stream URL -> (cancel_event, video_task, audio_task, chat_task)
agent_cache = {}
all_agents_fetched = False
gevent_pool = Pool(15)  # Initial pool size, adjusted in initialize_monitoring

# Directory for transcriptions
TRANSCRIPTION_DIR = os.getenv('TRANSCRIPTION_DIR', '/home/kvsh1m/LiveStream_Monitoring_Vue3_Flask/backend/transcriptions/')

def initialize_monitoring():
    """Initialize all monitoring components with resource monitoring."""
    logger.info('Initializing monitoring globals.')
    global _whisper_model, _sentiment_analyzer, gevent_pool
    
    # Dynamically adjust gevent pool size based on system resources
    cpu_count = psutil.cpu_count()
    memory_info = psutil.virtual_memory()
    available_memory_gb = memory_info.available / (1024 ** 3)  # Convert to GB
    max_tasks = max(10, min(cpu_count * 4, int(available_memory_gb * 2)))
    gevent_pool = Pool(max_tasks)
    logger.info(f'Initialized gevent pool with {max_tasks} workers based on {cpu_count} CPUs and {available_memory_gb:.2f} GB available memory.')
    
    # Log configuration status
    logger.info(f'Monitoring configurations: '
                f'ENABLE_MONITORING={os.getenv("ENABLE_MONITORING", "true")}, '
                f'ENABLE_VIDEO_MONITORING={os.getenv("ENABLE_VIDEO_MONITORING", "true")}, '
                f'ENABLE_AUDIO_MONITORING={os.getenv("ENABLE_AUDIO_MONITORING", "true")}, '
                f'ENABLE_CHAT_MONITORING={os.getenv("ENABLE_CHAT_MONITORING", "true")}')
    
    # Load models
    load_whisper_model()
    load_yolo_model()
    load_sentiment_analyzer()
    
    # Initialize audio processing
    try:
        from audio_processing import initialize_audio_globals
        initialize_audio_globals(
            whisper_model=_whisper_model
        )
    except ImportError as e:
        logger.warning(f'Failed to initialize audio processing: {e}')
    
    # Initialize video processing
    try:
        from video_processing import initialize_video_globals
        initialize_video_globals(
            yolo_model=_yolo_model
        )
    except ImportError as e:
        logger.warning(f'Failed to initialize video processing: {e}')
    
    # Initialize chat processing
    try:
        initialize_chat_globals(
            sentiment_analyzer=_sentiment_analyzer,
            enable_chat_monitoring=True,
            negative_sentiment_threshold=float(os.getenv('NEGATIVE_SENTIMENT_THRESHOLD', -0.5))
        )
        configure_smart_filter(
            time_window_minutes=float(os.getenv('CHAT_ALERT_COOLDOWN', 60)) / 60
        )
    except ImportError as e:
        logger.warning(f'Failed to initialize chat processing: {e}')

def load_whisper_model():
    """Load Whisper model for audio processing."""
    global _whisper_model
    if _whisper_model is None:
        try:
            import whisper
            model_size = os.getenv('WHISPER_MODEL_SIZE', 'tiny')
            logger.info(f'Loading Whisper model: {model_size}')
            _whisper_model = whisper.load_model(model_size)
            logger.info(f'Whisper model "{model_size}" loaded.')
        except ImportError as e:
            logger.error(f'Failed to import Whisper model: {e}')
            _whisper_model = None
        except Exception as e:
            logger.error(f'Error loading Whisper model: {e}')
            _whisper_model = None
    return _whisper_model

def load_yolo_model():
    """Load YOLO model for video processing."""
    global _yolo_model
    if _yolo_model is None:
        try:
            from ultralytics import YOLO
            import torch
            logger.info(f'PyTorch version: {torch.__version__}')
            torch.backends.nnpack.enabled = False
            _yolo_model = YOLO('yolo11n.pt', verbose=False)
            logger.info('YOLO model loaded successfully')
        except ImportError as e:
            logger.error(f'Failed to import YOLO model: {e}')
            _yolo_model = None
        except Exception as e:
            logger.error(f'Error loading YOLO model: {e}')
            _yolo_model = None
    return _yolo_model

def load_sentiment_analyzer():
    """Load sentiment analyzer for chat processing."""
    global _sentiment_analyzer
    if _sentiment_analyzer is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _sentiment_analyzer = SentimentIntensityAnalyzer()
            logger.info('Sentiment analyzer loaded.')
            return _sentiment_analyzer
        except ImportError as e:
            logger.error(f'Failed to import sentiment analyzer: {e}')
            _sentiment_analyzer = None
        except Exception as e:
            logger.error(f'Error loading sentiment analyzer: {e}')
            _sentiment_analyzer = None
    return _sentiment_analyzer

def refresh_flagged_keywords():
    """Get all flagged keywords from database."""
    try:
        with current_app.app_context():
            keywords = [kw.keyword.lower() for kw in ChatKeyword.query.all()]
        return keywords
    except Exception as e:
        logger.error(f'Error refreshing flagged keywords: {str(e)}')
        return []

def refresh_flagged_objects():
    """Get all flagged objects from database."""
    try:
        with current_app.app_context():
            objects = FlaggedObject.query.all()
            flagged = {obj.object_name.lower(): float(obj.confidence_threshold) for obj in objects}
        return flagged
    except Exception as e:
        logger.error(f'Error refreshing flagged objects: {str(e)}')
        return {}

def get_m3u8_url(stream):
    """Get M3U8 URL for a stream."""
    try:
        with current_app.app_context():
            logger.debug(f'Fetching M3U8 URL for stream {stream.id} (type: {stream.type})')
            if stream.type.lower() == 'chaturbate':
                cb_stream = ChaturbateStream.query.get(stream.id)
                url = cb_stream.chaturbate_m3u8_url if cb_stream else None
                logger.debug(f'Chaturbate M3U8 URL: {url}')
                return url
            elif stream.type.lower() == 'stripchat':
                sc_stream = StripchatStream.query.get(stream.id)
                url = sc_stream.stripchat_m3u8_url if sc_stream else None
                logger.debug(f'Stripchat M3U8 URL: {url}')
                return url
        return None
    except Exception as e:
        logger.error(f'Error getting M3U8 URL for stream {stream.id}: {str(e)}')
        return None

def fetch_all_agents():
    """Cache all agent usernames."""
    global all_agents_fetched, agent_cache
    
    if all_agents_fetched:
        return
        
    try:
        with current_app.app_context():
            agents = User.query.filter_by(role='agent').all()
            agent_cache = {agent.id: agent.username or f'agent_{agent.id}' for agent in agents}
            all_agents_fetched = True
            logger.info(f'Cached {len(agent_cache)} agent usernames.')
    except Exception as e:
        logger.error(f'Error fetching all agents: {str(e)}')

def fetch_agent_username(agent_id):
    """Fetch an agent username by ID."""
    if agent_id in agent_cache:
        return agent_cache[agent_id]
        
    try:
        with current_app.app_context():
            agent = User.query.get(agent_id)
            if agent:
                username = agent.username or f'agent_{agent_id}'
                agent_cache[agent_id] = username
                return username
            logger.warning(f'Agent {agent_id} not found.')
            agent_cache[agent_id] = f'Agent {agent_id}'
            return agent_cache[agent_id]
    except Exception as e:
        logger.error(f'Error fetching username for agent {agent_id}: {str(e)}')
        agent_cache[agent_id] = f'Agent {agent_id}'
        return agent_cache[agent_id]

def get_stream_info(stream_url):
    """Get platform and streamer info from stream URL."""
    try:
        with current_app.app_context():
            logger.debug(f'Looking up stream info for {stream_url}')
            # Check room_url first
            stream = Stream.query.filter_by(room_url=stream_url).first()
            if stream:
                logger.debug(f'Found stream by room_url: {stream_url}, type: {stream.type}, username: {stream.streamer_username}')
                return stream.type.lower(), stream.streamer_username
            
            # Check for Chaturbate Stream URL
            cb_stream = ChaturbateStream.query.filter_by(chaturbate_m3u8_url=stream_url).first()
            if cb_stream:
                stream = Stream.query.get(cb_stream.id)
                logger.debug(f'Found stream by chaturbate_m3u8_url: {stream_url}, type: chaturbate, username: {stream.streamer_username if stream else "unknown"}')
                return 'chaturbate', stream.streamer_username if stream else 'unknown'
            
            # Check for StripchatStream M3u8 URL
            sc_stream = StripchatStream.query.filter_by(stripchat_m3u8_url=stream_url).first()
            if sc_stream:
                stream = Stream.query.get(sc_stream.id)
                logger.debug(f'Found stream by stripchat_m3u8_url: {stream_url}, type: stripchat, username: {stream.streamer_username if stream else "unknown"}')
                return 'stripchat', stream.streamer_username if stream else 'unknown'
            
            logger.warning(f'No stream found for {stream_url}.')
            return 'unknown', 'unknown'
    except Exception as e:
        logger.error(f'Error getting stream info for {stream_url}: {str(e)}')
        return 'unknown', 'unknown'

def get_stream_assignment(stream_url):
    """Get assignment info for a stream."""
    try:
        with current_app.app_context():
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
                logger.warning(f'No stream found for {stream_url}.')
                return None, None
                
            assignments = Assignment.query.options(
                joinedload(Assignment.agent),
                joinedload(Assignment.stream)
            ).filter_by(stream_id=stream.id).all()
            
            if not assignments:
                logger.info(f'No assignments found for stream: {stream_url}.')
                return None, None
                
            assignment = assignments[0]
            agent_id = assignment.agent_id
            fetch_agent_username(agent_id)
            return assignment.id, agent_id
    except Exception as e:
        logger.error(f'Error getting stream assignment for {stream_url}: {str(e)}')
        return None, None

def save_transcription_to_json(stream_url, transcript, detected_keywords):
    """Save transcription data to a JSON file."""
    try:
        os.makedirs(TRANSCRIPTION_DIR, exist_ok=True)
        url_hash = hashlib.md5(str(stream_url).encode()).hexdigest()[:8]
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'transcription_{url_hash}_{timestamp}.json'
        filepath = os.path.join(TRANSCRIPTION_DIR, filename)
        
        data = {
            'stream_url': str(stream_url),
            'timestamp': datetime.now().isoformat(),
            'transcription': str(transcript),
            'detected_keywords': detected_keywords
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f'Saved transcription to {filepath}.')
    except Exception as e:
        logger.error(f'Error saving transcription to JSON for {stream_url}: {str(e)}')

def check_stream_availability(stream_url, timeout=10):
    """Check if a stream URL is accessible."""
    try:
        response = requests.head(stream_url, timeout=timeout)
        logger.info(f'Stream {stream_url} check: {response.status_code}')
        return response.status_code == 200
    except requests.exceptions.RequestException as e:
        logger.error(f'Error checking stream availability for {stream_url}: {str(e)}')
        return False

def process_video_detection(app, stream_url, stream_id, room_url, streamer_username, cancel_event):
    """Process video detection for a stream in a separate greenlet."""
    logger.info(f'Starting video detection for {stream_url} (stream_id: {stream_id})')
    max_retries = 3
    retry_delay = 10
    frame_process_times = {}
    video_interval = float(os.getenv('VIDEO_INTERVAL', '30'))

    with app.app_context():
        while not cancel_event.is_set():
            try:
                stream = Stream.query.get(stream_id)
                if not stream:
                    logger.error(f'Stream with ID {stream_id} no longer exists.')
                    break
                db.session.refresh(stream)
                if stream.status == 'offline':
                    logger.info(f'Stopping video monitoring for offline stream: {stream.id}.')
                    stop_monitoring(stream)
                    break
            except Exception as e:
                logger.error(f'Error refreshing stream {stream_id} for video monitoring: {str(e)}')
                break

            retry_count = 0
            stream_available = False

            while retry_count < max_retries and not cancel_event.is_set():
                if check_stream_availability(stream_url):
                    stream_available = True
                    break
                retry_count += 1
                logger.warning(f'Stream {stream_url} unavailable, retry {retry_count}/{max_retries}.')
                gevent.sleep(retry_delay)

            if not stream_available:
                logger.error(f'Stream {stream_url} is offline after {max_retries} retries.')
                stream = Stream.query.get(stream_id)
                if stream:
                    with app.app_context():
                        stream.status = 'offline'
                        db.session.commit()
                    stop_monitoring(stream)
                break

            try:
                video_container = av.open(stream_url, timeout=60)
                video_stream = next((s for s in video_container.streams if s.type == 'video'), None)

                if video_stream:
                    for packet in video_container.demux(video_stream):
                        if cancel_event.is_set():
                            break
                        try:
                            for frame in packet.decode():
                                frame_time = frame.pts * float(video_stream.time_base)
                                last_process_time = frame_process_times.get(stream_url, 0)

                                if last_process_time is None or frame_time - last_process_time >= video_interval:
                                    img = frame.to_ndarray(format='bgr24')
                                    img = cv2.resize(img, (640, 480))

                                    detections = process_video_frame(img, stream_url)
                                    if detections:
                                        log_video_detection(detections, img, stream_url)

                                    frame_process_times[stream_url] = frame_time
                                    break  # Process one frame per interval
                            break  # Exit after processing one packet
                        except av.error.InvalidDataError:
                            logger.debug(f'Invalid video data for {stream_url}, skipping packet.')
                            continue
                        except Exception as e:
                            logger.error(f'Error decoding video packet for {stream_url}: {str(e)}')
                            continue
                    video_container.close()
                else:
                    logger.warning(f'No video stream found for {stream_url}')
                    gevent.sleep(video_interval)

            except av.error.EOFError:
                stream = Stream.query.get(stream_id)
                if stream:
                    stream.status = 'offline'
                    db.session.commit()
                    stop_monitoring(stream)
                break
            except Exception as e:
                logger.error(f'Error opening video stream {stream_url}: {str(e)}')
                gevent.sleep(retry_delay)
                continue

            gevent.sleep(video_interval)

        logger.info(f'Stopped video monitoring for {stream_url}.')

def process_audio_detection(app, stream_url, stream_id, room_url, streamer_username, cancel_event):
    """Process audio detection for a stream in a separate greenlet."""
    logger.info(f'Starting audio detection for {stream_url} (stream_id: {stream_id})')
    audio_interval = float(os.getenv('AUDIO_DETECTION_INTERVAL', 30))
    max_retries = 3
    retry_delay = 10

    with app.app_context():
        while not cancel_event.is_set():
            try:
                stream = Stream.query.get(stream_id)
                if not stream:
                    logger.error(f'Stream with ID {stream_id} no longer exists.')
                    break
                db.session.refresh(stream)
                if stream.status == 'offline':
                    logger.info(f'Stopping audio monitoring for offline stream: {stream.id}.')
                    stop_monitoring(stream)
                    break
            except Exception as e:
                logger.error(f'Error refreshing stream {stream_id} for audio processing: {str(e)}')
                break

            retry_count = 0
            stream_available = False

            while retry_count < max_retries and not cancel_event.is_set():
                if check_stream_availability(stream_url):
                    stream_available = True
                    break
                retry_count += 1
                logger.warning(f'Stream {stream_url} unavailable, retry {retry_count}/{max_retries}.')
                gevent.sleep(retry_delay)

            if not stream_available:
                logger.error(f'Stream {stream_url} is offline after {max_retries} retries.')
                stream = Stream.query.get(stream_id)
                if stream:
                    stream.status = 'offline'
                    db.session.commit()
                    stop_monitoring(stream)
                break

            try:
                audio_container = av.open(stream_url, timeout=60)
                audio_stream = next((s for s in audio_container.streams if s.type == 'audio'), None)

                if audio_stream:
                    audio_buffer = []
                    total_audio_duration = 0
                    sample_rate = audio_stream.rate if audio_stream else 16000

                    for packet in audio_container.demux(audio_stream):
                        if cancel_event.is_set():
                            break
                        try:
                            for frame in packet.decode():
                                audio_data = frame.to_ndarray().flatten().astype(np.float32) / 32768.0
                                frame_duration = frame.samples / sample_rate
                                audio_buffer.append(audio_data)
                                total_audio_duration += frame_duration

                                if total_audio_duration >= current_app.config.get('AUDIO_SAMPLE_DURATION', 30):
                                    combined_audio = np.concatenate(audio_buffer)
                                    detections, transcript = process_audio_segment(combined_audio, sample_rate, stream_url)

                                    detected_keywords = []
                                    if transcript:
                                        keywords = refresh_flagged_keywords()
                                        detected_keywords = [kw for kw in keywords if kw in transcript.lower()]
                                        if detected_keywords:
                                            logger.info(f'Keywords detected in audio for {stream_url}: {detected_keywords}')

                                    save_transcription_to_json(stream_url, transcript, detected_keywords)

                                    if detections:
                                        for detection in detections:
                                            log_audio_detection(detection, stream_url)
                                            platform, streamer = get_stream_info(room_url)
                                            notification_data = {
                                                'event_type': 'audio_keyword_alert',
                                                'timestamp': detection['timestamp'],
                                                'details': {
                                                    'keyword': detection['keyword'],
                                                    'transcript': transcript,
                                                    'streamer_name': streamer,
                                                    'platform': platform,
                                                    'stream_url': stream_url
                                                },
                                                'read': False,
                                                'room_url': room_url,
                                                'streamer': streamer,
                                                'platform': platform,
                                                'assigned_agent': 'Unassigned'
                                            }
                                            emit_notification(notification_data)

                                    audio_buffer = []
                                    total_audio_duration = 0
                                    break
                            break
                        except Exception as e:
                            logger.error(f'Error processing audio frame for {stream_url}: {str(e)}')
                            continue
                    audio_container.close()
                else:
                    logger.warning(f'No audio stream found for {stream_url}')
                    gevent.sleep(audio_interval)

            except av.error.OSError as e:
                logger.error(f'OS error opening audio stream {stream_url}: {str(e)}')
                gevent.sleep(retry_delay)
            except Exception as e:
                logger.error(f'Error opening audio stream {stream_url}: {str(e)}')
                gevent.sleep(retry_delay)

            gevent.sleep(audio_interval)

        logger.info(f'Stopped audio monitoring for {stream_url}.')

def process_chat_detection(app, room_url, stream_id, streamer_username, cancel_event):
    """Process chat detection for a stream in a separate greenlet."""
    logger.info(f'Starting chat detection for {room_url} (stream_id: {stream_id})')
    chat_interval = float(os.getenv('CHAT_DETECTION_INTERVAL', 30))

    with app.app_context():
        while not cancel_event.is_set():
            try:
                stream = Stream.query.get(stream_id)
                if not stream:
                    logger.error(f'Stream with ID {stream_id} no longer exists.')
                    break
                db.session.refresh(stream)
                if stream.status == 'offline':
                    logger.info(f'Stopping chat monitoring for offline stream: {stream.id}.')
                    stop_monitoring(stream)
                    break
            except Exception as e:
                logger.error(f'Error refreshing stream {stream_id} for chat monitoring: {str(e)}')
                break

            try:
                logger.debug(f'Fetching chat messages for {streamer_username} at {room_url}.')
                messages = fetch_chat_messages(room_url)

                if messages:
                    logger.debug(f'Processing {len(messages)} chat messages for {streamer_username}.')
                    chat_detections = process_chat_messages(messages, room_url)

                    if chat_detections:
                        logger.info(f'Detected {len(chat_detections)} chat issues for {streamer_username}.')
                        log_chat_detection(chat_detections, room_url)

                        for detection in chat_detections:
                            notification_data = {
                                'event_type': 'chat_alert',
                                'timestamp': datetime.now().isoformat(),
                                'details': {
                                    'type': detection.get('type'),
                                    'message': detection.get('message'),
                                    'username': detection.get('username'),
                                    'streamer_name': streamer_username,
                                    'platform': stream.type.lower(),
                                    'room_url': room_url,
                                    'stream_url': stream.room_url
                                },
                                'read': False,
                                'room_url': room_url,
                                'streamer': streamer_username,
                                'platform': stream.type.lower(),
                                'assigned_agent': 'Unassigned'
                            }
                            emit_notification(notification_data)
                else:
                    logger.debug(f'No chat messages fetched for {streamer_username} at {room_url}.')
            except Exception as e:
                logger.error(f'Error processing chat for {streamer_username} ({room_url}): {str(e)}')
                gevent.sleep(10)

            gevent.sleep(chat_interval)

        logger.info(f'Stopped chat monitoring for {room_url}.')

def get_stream_url(stream):
    """Get the appropriate stream URL for monitoring."""
    m3u8_url = get_m3u8_url(stream)
    return m3u8_url if m3u8_url else stream.room_url

def start_monitoring(stream):
    """Start monitoring a stream with separate video, audio, and chat detection."""
    try:
        stream_id = stream.id
        stream_url = get_m3u8_url(stream)  # Use M3U8 for video/audio
        room_url = stream.room_url  # Use room_url for chat
        streamer_username = stream.streamer_username
        
        if not stream_url or not room_url:
            logger.error(f'No valid URLs for stream {stream_id} - {streamer_username}: stream_url={stream_url}, room_url={room_url}')
            return False
            
        if stream_url in stream_processors:
            logger.info(f'Stream {stream_id} - {streamer_username} is already being monitored.')
            return True
            
        logger.info(f'Starting monitoring for stream {stream_id} - {streamer_username} at stream_url={stream_url}, room_url={room_url}')
        
        app = current_app._get_current_object()
        cancel_event = gevent.event.Event()
        
        # Spawn separate greenlets for video, audio, and chat detection
        enable_video_monitoring = os.getenv('ENABLE_VIDEO_MONITORING', 'true').lower() == 'true'
        enable_audio_monitoring = os.getenv('ENABLE_AUDIO_MONITORING', 'true').lower() == 'true'
        enable_chat_monitoring = os.getenv('ENABLE_CHAT_MONITORING', 'true').lower() == 'true'
        
        video_task = None
        audio_task = None
        chat_task = None
        
        if enable_video_monitoring:
            video_task = gevent_pool.spawn(
                process_video_detection,
                app,
                stream_url,
                stream_id,
                room_url,
                streamer_username,
                cancel_event
            )
        if enable_audio_monitoring:
            audio_task = gevent_pool.spawn(
                process_audio_detection,
                app,
                stream_url,
                stream_id,
                room_url,
                streamer_username,
                cancel_event
            )
        if enable_chat_monitoring:
            chat_task = gevent_pool.spawn(
                process_chat_detection,
                app,
                room_url,
                stream_id,
                streamer_username,
                cancel_event
            )
        
        stream_processors[stream_url] = (cancel_event, video_task, audio_task, chat_task)
        
        with app.app_context():
            try:
                stream = Stream.query.get(stream_id)
                if stream:
                    stream.is_monitored = True
                    stream.status = 'monitoring'
                    db.session.commit()
                    emit_stream_update({
                        'id': stream.id,
                        'url': stream.room_url,
                        'status': 'monitoring',
                        'type': stream.type,
                        'isDetecting': True
                    })
            except Exception as e:
                logger.error(f'Failed to update stream status for {stream_id}: {str(e)}')
                
            logger.info(f'Monitoring started for stream {stream_id} - {streamer_username}: '
                        f'video={enable_video_monitoring}, audio={enable_audio_monitoring}, chat={enable_chat_monitoring}')
            return True
            
    except Exception as e:
        logger.error(f'Error starting monitoring for stream {stream_id}: {str(e)}')
        return False

def stop_monitoring(stream):
    """Stop monitoring a stream."""
    if not stream:
        logger.error('Stream not provided.')
        return
        
    try:
        stream_id = stream.id
        stream_url = get_m3u8_url(stream) or stream.room_url
        
        with current_app.app_context():
            try:
                stream = Stream.query.get(stream_id)
                if stream:
                    stream.is_monitored = False
                    stream.status = 'offline'
                    db.session.commit()
            except Exception as e:
                logger.error(f'Failed to update stream status for {stream_id}: {str(e)}')
                
        if stream_url in stream_processors:
            try:
                cancel_event, video_task, audio_task, chat_task = stream_processors.get(stream_url, (None, None, None, None))
                if cancel_event:
                    cancel_event.set()
                if video_task:
                    gevent.joinall([video_task], timeout=2.0)
                if audio_task:
                    gevent.joinall([audio_task], timeout=2.0)
                if chat_task:
                    gevent.joinall([chat_task], timeout=2.0)
                del stream_processors[stream_url]
            except Exception as e:
                logger.error(f'Error cleaning up stream_processors for {stream_url}: {str(e)}')
                
        from video_processing import cleanup_video_resources
        from audio_processing import cleanup_audio_resources
        try:
            cleanup_video_resources()
            cleanup_audio_resources()
        except Exception as e:
            logger.error(f'Error cleaning up video/audio resources for stream {stream.id}: {str(e)}')
            
        logger.info(f'Stopped monitoring for stream: {stream.id}.')
        try:
            emit_stream_update({
                'id': stream.id,
                'url': stream_url,
                'status': 'stopped',
                'type': stream.type,
                'isDetecting': False
            })
        except Exception as e:
            logger.error(f'Error emitting stream update for {stream_url}: {str(e)}')
            
    except Exception as e:
        logger.error(f'Error stopping monitoring for stream {stream.id}: {str(e)}')

def fetch_new_streams_from_platforms():
    """Fetch new or unmonitored streams from the database."""
    try:
        with current_app.app_context():
            # Use with_polymorphic to fetch Stream and its subclasses (ChaturbateStream, StripchatStream)
            stream_types = with_polymorphic(Stream, [ChaturbateStream, StripchatStream])
            streams = Stream.query.with_polymorphic([ChaturbateStream, StripchatStream]).filter(
                Stream.status == 'online',
                Stream.is_monitored == False
            ).options(
                joinedload(stream_types.assignments)
            ).all()
            
            logger.info(f'Found {len(streams)} new or unmonitored online streams.')
            
            for stream in streams:
                try:
                    logger.debug(f'Processing stream {stream.id} - {stream.streamer_username}: type={stream.type}, status={stream.status}')
                    # Attempt to start monitoring for unmonitored online streams
                    auto_start_monitoring_on_online(stream)
                    
                    # Emit update for each stream
                    emit_stream_update({
                        'id': stream.id,
                        'url': stream.room_url,
                        'status': stream.status,
                        'type': stream.type,
                        'streamer_username': stream.streamer_username,
                        'isDetecting': stream.is_monitored
                    })
                    logger.debug(f'Emitted stream update for stream {stream.id} - {stream.streamer_username}')
                except Exception as e:
                    logger.error(f'Error processing stream {stream.id}: {str(e)}')
                    continue
            
            return [s.id for s in streams]
            
    except Exception as e:
        logger.error(f'Error fetching new streams from database: {str(e)}')
        return []

def refresh_and_monitor_streams(stream_ids):
    """Refresh and start monitoring streams."""
    try:
        with current_app.app_context():
            streams = Stream.query.filter(Stream.id.in_(stream_ids)).all()
            if not streams:
                logger.warning('No streams found for provided IDs.')
                return False
                
            for stream in streams:
                try:
                    logger.debug(f'Refreshing stream {stream.id} - {stream.streamer_username}')
                    db.session.refresh(stream)
                    
                    if stream.status != 'online':
                        logger.info(f'Skipping stream {stream.id} - {stream.streamer_username}: not online (status={stream.status})')
                        continue
                        
                    if stream.is_monitored:
                        logger.info(f'Stream {stream.id} - {stream.streamer_username} is already monitored.')
                        continue
                        
                    # Attempt to start monitoring
                    auto_start_monitoring_on_online(stream)
                    
                except Exception as e:
                    logger.error(f'Error processing stream {stream.id}: {str(e)}')
                    continue
                    
            return True
            
    except Exception as e:
        logger.error(f'Error refreshing and monitoring streams: {str(e)}')
        return False

def start_notification_monitor(clean_start=False):
    """Initialize the notification monitoring system without starting any stream monitoring."""
    logger.info('Starting notification monitor.')
    try:
        with current_app.app_context():
            app = current_app._get_current_object()
            # No automatic starting of stream monitoring
            logger.info('Notification monitor initialized without automatic stream monitoring.')
    except Exception as e:
        logger.error(f'Error starting notification monitor: {str(e)}')
        raise

def monitor_new_streams(app):
    """Background task to monitor for new streams."""
    check_interval = int(os.getenv('CHECK_INTERVAL', 900))
    
    while True:
        try:
            gevent.sleep(check_interval)
            logger.debug('Checking for new or online streams.')
            
            with app.app_context():
                stream_ids = fetch_new_streams_from_platforms()
                logger.info(f'Found {len(stream_ids)} new or unmonitored streams.')
                if stream_ids:
                    refresh_and_monitor_streams(stream_ids)
                    logger.info(f'Attempted to refresh and monitor {len(stream_ids)} streams.')
                else:
                    logger.debug('No new or unmonitored streams found.')
                    
        except Exception as e:
            logger.error(f'Error in monitor_new_streams: {str(e)}')
            gevent.sleep(check_interval)

def retry_failed_streams(app):
    """Background task to retry failed streams."""
    retry_interval = int(os.getenv('RETRY_INTERVAL', 3600))
    
    while True:
        try:
            gevent.sleep(retry_interval)
            
            with current_app.app_context():
                failed_streams = Stream.query.filter(
                    Stream.is_monitored == False,
                    Stream.status == 'online'
                ).all()
                
                if failed_streams:
                    stream_ids = [stream.id for stream in failed_streams]
                    logger.info(f'Found {len(failed_streams)} unmonitored online streams to retry.')
                    refresh_and_monitor_streams(stream_ids)
                    logger.info(f'Attempted to retry {len(failed_streams)} failed online streams.')
                else:
                    logger.debug('No unmonitored online streams to retry.')
                    
        except Exception as e:
            logger.error(f'Error in retry_failed_streams: {str(e)}')
            gevent.sleep(retry_interval)

def get_monitoring_status():
    """Get current monitoring status for all streams."""
    try:
        with current_app.app_context():
            streams = Stream.query.all()
            status = {
                'total_streams': len(streams),
                'monitored_streams': sum(1 for s in streams if s.is_monitored),
                'online_streams': sum(1 for s in streams if s.status == 'online'),
                'active_processors': len(stream_processors),
                'enable_monitoring': True,
                'continuous_monitoring': os.getenv('CONTINUOUS', 'true').lower() == 'true',
                'streams': []
            }
            
            for stream in streams:
                stream_url = get_m3u8_url(stream) or stream.room_url
                stream_info = {
                    'id': stream.id,
                    'streamer': stream.streamer_username,
                    'platform': stream.type,
                    'status': stream.status,
                    'is_monitored': stream.is_monitored,
                    'has_processor': stream_url in stream_processors
                }
                status['streams'].append(stream_info)
                
            return status
    except Exception as e:
        logger.error(f'Error getting monitoring status: {str(e)}')
        return None

def restart_all_streams():
    """Restart all monitoring for online streams."""
    logger.info('Restarting all monitoring for online streams.')
    
    try:
        with current_app.app_context():
            streams = Stream.query.filter(Stream.is_monitored == True).all()
            for stream in streams:
                try:
                    stop_monitoring(stream)
                except Exception as e:
                    logger.error(f'Error stopping monitoring for {stream.id}: {str(e)}')
                    continue
            
            gevent.sleep(2)
            
            try:
                start_notification_monitor(clean_start=True)
                logger.info('Monitoring restarted.')
                return True
            except Exception as e:
                logger.error(f'Error restarting monitoring: {str(e)}')
                return False
                
    except Exception as e:
        logger.error(f'Error restarting all monitoring: {str(e)}')
        return False

def schedule_periodic_detection(app, interval=3600):
    """Schedule periodic detection for online streams."""
    logger.info(f'Scheduling periodic detection with interval {interval} seconds.')
    while True:
        try:
            with app.app_context():
                streams = Stream.query.filter(
                    Stream.status == 'online'
                ).all()
                logger.info(f'Found {len(streams)} online streams for periodic detection.')
                for stream in streams:
                    try:
                        db.session.refresh(stream)
                        logger.debug(f'Checking stream {stream.id} - {stream.streamer_username}: status={stream.status}, is_monitored={stream.is_monitored}')
                        auto_start_monitoring_on_online(stream)
                    except Exception as e:
                        logger.error(f'Error processing stream {stream.id}: {str(e)}')
                        continue
                try:
                    db.session.commit()
                except Exception as e:
                    logger.error(f'Error committing session: {str(e)}')
                    db.session.rollback()
        except Exception as e:
            logger.error(f'Error in periodic detection: {str(e)}')
        gevent.sleep(interval)

def schedule_periodic_chat_detection(app, check_interval=900, success_cooldown=1800, max_seconds=600):
    """Schedule periodic chat detection for online streams."""
    logger.info(f'Scheduling periodic chat detection with interval {check_interval} seconds.')
    stream_last_success = {}
    
    while True:
        try:
            with app.app_context():
                streams = Stream.query.filter(
                    Stream.status == 'online'
                ).all()
                logger.info(f'Found {len(streams)} online streams for periodic chat.')
                
                for stream in streams:
                    room_url = stream.room_url
                    streamer_username = stream.streamer_username
                    current_time = time.time()
                    last_success = stream_last_success.get(room_url, 0)
                    
                    if current_time - last_success < success_cooldown:
                        logger.debug(f'Skipping chat for {streamer_username}: In cooldown.')
                        continue
                        
                    retry_count = 0
                    max_retries = 3
                    while retry_count < max_retries:
                        try:
                            messages = fetch_chat_messages(room_url)
                            if messages:
                                chat_detections = process_chat_messages(messages, room_url)
                                if chat_detections:
                                    logger.info(f'Detected {len(chat_detections)} chat issues for {streamer_username}')
                                    log_chat_detection(chat_detections, room_url)
                                    for detection in chat_detections:
                                        notification_data = {
                                            'event_type': 'chat_alert',
                                            'timestamp': datetime.now().isoformat(),
                                            'details': {
                                                'type': detection.get('type'),
                                                'message': detection.get('message'),
                                                'username': detection.get('username'),
                                                'streamer_name': streamer_username,
                                                'platform': stream.type.lower(),
                                                'room_url': stream.room_url,
                                                'stream_url': stream.room_url
                                            },
                                            'read': False,
                                            'room_url': room_url,
                                            'streamer': streamer_username,
                                            'platform': stream.type.lower(),
                                            'assigned_agent': 'Unassigned'
                                        }
                                        emit_notification(notification_data)
                                stream_last_success[room_url] = current_time
                                break
                            logger.debug(f'No chat messages for {streamer_username}')
                            break
                        except Exception as e:
                            retry_count += 1
                            logger.warning(f'Chat detection error for {streamer_username}: retry {retry_count}/{max_retries}: {str(e)}')
                            if retry_count >= max_retries:
                                logger.error(f'Max retries exceeded for {streamer_username}')
                                break
                            gevent.sleep(10)
        except Exception as e:
            logger.error(f'Error in periodic chat detection: {str(e)}')
        gevent.sleep(check_interval)

def auto_start_monitoring_on_online(stream):
    """Automatically start monitoring when a stream's status changes to online."""
    try:
        stream_url = get_m3u8_url(stream) or stream.room_url
        logger.debug(f'Checking auto-start for stream {stream.id} - {stream.streamer_username}: status={stream.status}, is_monitored={stream.is_monitored}, stream_url={stream_url}, room_url={stream.room_url}')
        
        if stream.status != 'online':
            logger.info(f'Stream {stream.id} - {stream.streamer_username} is not online (status={stream.status}), skipping.')
            return
            
        if stream.is_monitored:
            logger.debug(f'Stream {stream.id} - {stream.streamer_username} is already monitored.')
            return
            
        if not stream_url or not stream.room_url:
            logger.error(f'No valid URLs for stream {stream.id} - {stream.streamer_username}: stream_url={stream_url}, room_url={stream.room_url}')
            NotificationService.notify_admins(
                event_type='system_alert',
                details={
                    'message': f'No valid URL for stream {stream.streamer_username}',
                    'room_url': stream.room_url,
                    'streamer_username': stream.streamer_username,
                    'platform': stream.type
                },
                room_url=stream.room_url,
                platform=stream.type,
                streamer=stream.streamer_username,
                priority='high'
            )
            return
            
        if not check_stream_availability(stream_url):
            logger.warning(f'Stream {stream.id} - {stream.streamer_username} is not accessible at {stream_url}')
            with current_app.app_context():
                stream.status = 'offline'
                db.session.commit()
            return
            
        logger.info(f'Auto-starting monitoring for {stream.id} - {stream.streamer_username}')
        success = start_monitoring(stream)
        if success:
            logger.info(f'Successfully started monitoring for {stream.id} - {stream.streamer_username}')
            with current_app.app_context():
                stream = Stream.query.get(stream.id)
                if stream:
                    stream.is_monitored = True
                    stream.status = 'monitoring'
                    db.session.commit()
                    emit_stream_update({
                        'id': stream.id,
                        'url': stream.room_url,
                        'status': 'monitoring',
                        'type': stream.type,
                        'isDetecting': True
                    })
        else:
            logger.error(f'Failed to auto-start monitoring for {stream.id} - {stream.streamer_username}')
            NotificationService.notify_admins(
                event_type='system_alert',
                details={
                    'message': f'Failed to auto-start monitoring for {stream.streamer_username}',
                    'room_url': stream.room_url,
                    'streamer_username': stream.streamer_username,
                    'platform': stream.type
                },
                room_url=stream.room_url,
                platform=stream.type,
                streamer=stream.streamer_username,
                priority='high'
            )
    except Exception as e:
        logger.error(f'Error in auto_start_monitoring for {stream.id}: {str(e)}')
        NotificationService.notify_admins(
            event_type='system_alert',
            details={
                'message': f'Error auto-starting monitoring for {stream.streamer_username}: {str(e)}',
                'room_url': stream.room_url,
                'streamer_username': stream.streamer_username,
                'platform': stream.type
            },
            room_url=stream.room_url,
            platform=stream.type,
            streamer=stream.streamer_username,
            priority='high'
        )

# Exports
__all__ = [
    'start_monitoring',
    'stop_monitoring',
    'process_audio_segment',
    'process_video_frame',
    'process_chat_messages',
    'start_notification_monitor',
    'refresh_and_monitor_streams',
    'initialize_monitoring',
    'auto_start_monitoring_on_online'
]