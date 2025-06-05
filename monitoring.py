# monitoring.py
"""
Monitoring module for livestream content analysis.
Fixed version that properly handles Flask application context with gevent greenlets.
"""
import logging
import gevent
from gevent import Greenlet
from gevent.lock import Semaphore
from models import Stream, ChaturbateStream, StripchatStream, DetectionLog
from extensions import db
from video_processing import load_yolo_model, process_video_frame, log_video_detection, cleanup_video_resources
from audio_processing import load_whisper_model, process_audio_segment, log_audio_detection, cleanup_audio_resources
from chat_processing import fetch_chat_messages, process_chat_messages, log_chat_detection
import cv2
import numpy as np
import av
import m3u8
import requests
import time
from datetime import datetime, timedelta
from utils.notifications import emit_stream_update
from collections import defaultdict
from flask import g

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Global state
stream_processors = {}  # Maps stream_url to Greenlet
monitoring_lock = Semaphore()
monitoring_status = defaultdict(dict)
last_notification_time = defaultdict(lambda: datetime.min)  # Track last notification per stream

class ContextGreenlet(Greenlet):
    """Custom Greenlet class that properly manages Flask application context"""
    
    def __init__(self, app, func, *args, **kwargs):
        # Store app and function details as instance variables instead of trying to set args
        self.app = app
        self.target_func = func
        self.target_args = args
        self.target_kwargs = kwargs
        # Initialize parent Greenlet without arguments
        super().__init__()

    def _run(self):
        """Run the function within Flask application context"""
        try:
            with self.app.app_context():
                return self.target_func(*self.target_args, **self.target_kwargs)
        except Exception as e:
            logger.error(f"Error in ContextGreenlet: {e}")
            raise

def get_stream_url(stream):
    """Get the appropriate m3u8 URL for a stream."""
    if isinstance(stream, ChaturbateStream):
        return stream.chaturbate_m3u8_url
    elif isinstance(stream, StripchatStream):
        return stream.stripchat_m3u8_url
    return stream.room_url

def fetch_m3u8_stream(m3u8_url):
    """Fetch and parse m3u8 playlist to get the streaming URL."""
    try:
        response = requests.get(m3u8_url, timeout=10)
        response.raise_for_status()
        playlist = m3u8.loads(response.text)
        if playlist.is_variant:
            highest_bandwidth = max(playlist.playlists, key=lambda p: p.stream_info.bandwidth or 0)
            return highest_bandwidth.uri
        return m3u8_url
    except Exception as e:
        logger.error(f"Error fetching m3u8 playlist from {m3u8_url}: {e}")
        return None

def monitor_stream(app, stream, stream_url):
    """Monitor video and audio streams using a single AV container."""
    logger.info(f"Starting stream monitoring greenlet for {stream_url}")
    
    try:
        # Load models within the application context
        yolo_model = load_yolo_model(app)
        whisper_model = load_whisper_model(app)
        
        # Allow monitoring to continue even if YOLO model is not loaded
        if not yolo_model:
            logger.warning(f"YOLO model not loaded for {stream_url}; video processing disabled")
        if not whisper_model:
            logger.warning(f"Whisper model not loaded for {stream_url}; audio processing disabled")
        if not yolo_model and not whisper_model:
            logger.error(f"No models loaded for {stream_url}; monitoring aborted")
            return

        logger.info(f"Starting stream monitoring for {stream_url}")
        stream_uri = fetch_m3u8_stream(stream_url)
        if not stream_uri:
            logger.error(f"No valid stream URI for {stream_url}")
            return

        try:
            container = av.open(stream_uri, options={'timeout': '10000000'})
        except Exception as e:
            logger.error(f"Failed to open stream {stream_uri}: {e}")
            return

        try:
            video_stream = next((s for s in container.streams if s.type == 'video'), None)
            audio_stream = next((s for s in container.streams if s.type == 'audio'), None)

            if not video_stream and not audio_stream:
                logger.error(f"No video or audio stream found for {stream_url}")
                return

            if not video_stream:
                logger.warning(f"No video stream found for {stream_url}; video processing disabled")
            if not audio_stream:
                logger.warning(f"No audio stream found for {stream_url}; audio processing disabled")
            else:
                sample_rate = audio_stream.codec_context.sample_rate
                logger.debug(f"Audio stream found for {stream_url}, sample_rate={sample_rate}")

            audio_buffer = []
            segment_duration = float(app.config.get('AUDIO_SEGMENT_LENGTH', 30))
            expected_samples = int(sample_rate * segment_duration) if audio_stream else 0
            logger.debug(f"Stream opened for {stream_url}, video_stream={bool(video_stream)}, audio_stream={bool(audio_stream)}")

            # Process stream packets
            streams_to_demux = []
            if video_stream:
                streams_to_demux.append(video_stream)
            if audio_stream:
                streams_to_demux.append(audio_stream)

            for packet in container.demux(streams_to_demux):
                # Check if monitoring should continue
                if not stream_processors.get(stream_url):
                    logger.info(f"Stopping stream monitoring for {stream_url}")
                    break

                try:
                    # Process video frames
                    if video_stream and packet.stream == video_stream and yolo_model:
                        for frame in packet.decode():
                            img = frame.to_ndarray(format='bgr24')
                            detections = process_video_frame(img, stream_url, app)
                            if detections:
                                logger.info(f"Video detections for {stream_url}: {detections}")
                                log_video_detection(detections, img, stream_url, app)

                    # Process audio frames
                    elif audio_stream and packet.stream == audio_stream and whisper_model:
                        for frame in packet.decode():
                            audio_data = frame.to_ndarray().flatten()
                            audio_amplitude = np.max(np.abs(audio_data)) if len(audio_data) > 0 else 0
                            logger.debug(f"Audio frame for {stream_url}: samples={len(audio_data)}, max_amplitude={audio_amplitude:.4f}")

                            if audio_amplitude < 1e-5:
                                logger.warning(f"Low amplitude audio frame for {stream_url}; may be silent")
                                continue

                            audio_buffer.extend(audio_data)
                            logger.debug(f"Audio buffer size for {stream_url}: {len(audio_buffer)} samples")

                            if len(audio_buffer) >= expected_samples:
                                audio_segment = np.array(audio_buffer[:expected_samples], dtype=np.float32)
                                audio_buffer = audio_buffer[expected_samples:]
                                logger.info(f"Processing audio segment for {stream_url}: duration={segment_duration}s, samples={len(audio_segment)}")
                                
                                try:
                                    detections, transcript = process_audio_segment(audio_segment, sample_rate, stream_url, app)
                                    if transcript:
                                        logger.info(f"Transcript for {stream_url}: {transcript[:100]}...")
                                    else:
                                        logger.warning(f"No transcript generated for {stream_url}")
                                    
                                    for detection in detections:
                                        logger.info(f"Audio detection for {stream_url}: {detection}")
                                        log_audio_detection(detection, stream_url, app)
                                except Exception as e:
                                    logger.error(f"Error processing audio segment for {stream_url}: {e}")

                except Exception as e:
                    logger.error(f"Error processing packet for {stream_url}: {e}")
                    continue

                # Yield to other greenlets
                gevent.sleep(0)

        finally:
            container.close()
            
    except Exception as e:
        logger.error(f"Error in stream monitoring for {stream_url}: {e}")
        
    finally:
        # Cleanup resources
        try:
            cleanup_video_resources(app)
            cleanup_audio_resources(app)
        except Exception as e:
            logger.error(f"Error cleaning up resources for {stream_url}: {e}")

def monitor_chat_stream(app, stream, stream_url):
    """Monitor chat messages for keywords and sentiment."""
    logger.info(f"Starting chat monitoring greenlet for {stream_url}")
    
    try:
        room_url = stream.room_url
        if not room_url:
            logger.error(f"No room_url for stream {stream_url}; chat monitoring aborted")
            return

        logger.info(f"Starting chat monitoring for {room_url}")
        chat_cooldown = app.config.get('CHAT_ALERT_COOLDOWN', 45)
        
        while stream_processors.get(stream_url):
            try:
                messages = fetch_chat_messages(room_url, app)
                if messages:
                    detections = process_chat_messages(messages, room_url, app)
                    for detection in detections:
                        logger.info(f"Chat detection for {room_url}: {detection}")
                        log_chat_detection([detection], room_url, app)
                else:
                    logger.debug(f"No chat messages fetched for {room_url}")
                    
            except Exception as e:
                logger.error(f"Chat fetch error for {room_url}: {e}")
                
            gevent.sleep(chat_cooldown)
            
    except Exception as e:
        logger.error(f"Error in chat monitoring for {room_url}: {e}")

def start_notification_monitor(app):
    """Monitor detection logs and send notifications for significant events."""
    logger.info("Starting notification monitor")
    
    try:
        notification_cooldown = app.config.get('NOTIFICATION_COOLDOWN', 300)  # 5 minutes
        
        while True:
            try:
                time_threshold = datetime.now() - timedelta(seconds=notification_cooldown)
                recent_detections = DetectionLog.query.filter(
                    DetectionLog.timestamp >= time_threshold
                ).all()

                for detection in recent_detections:
                    # Look up stream by room_url
                    stream = Stream.query.filter_by(room_url=detection.room_url).first()
                    if not stream:
                        cb_stream = ChaturbateStream.query.filter_by(chaturbate_m3u8_url=detection.room_url).first()
                        if cb_stream:
                            stream = Stream.query.get(cb_stream.id)
                        else:
                            sc_stream = StripchatStream.query.filter_by(stripchat_m3u8_url=detection.room_url).first()
                            if sc_stream:
                                stream = Stream.query.get(sc_stream.id)
                    
                    if not stream:
                        logger.warning(f"No stream found for detection room_url: {detection.room_url}")
                        continue

                    stream_url = get_stream_url(stream)
                    last_sent = last_notification_time[stream_url]
                    if (datetime.now() - last_sent).total_seconds() < notification_cooldown:
                        continue

                    detection_type = detection.event_type
                    details = detection.details
                    notification = {
                        'stream_id': stream.id,
                        'stream_url': stream_url,
                        'type': detection_type,
                        'details': details,
                        'timestamp': detection.timestamp.isoformat(),
                        'severity': 'high' if 'flagged' in str(details).lower() else 'medium'
                    }

                    emit_stream_update({
                        'event': 'detection_alert',
                        'data': notification
                    })
                    logger.info(f"Sent notification for {stream_url}: {notification}")

                    last_notification_time[stream_url] = datetime.now()

            except Exception as e:
                logger.error(f"Error processing notifications: {e}")
                
            gevent.sleep(60)
            
    except Exception as e:
        logger.error(f"Error in notification monitor: {e}")
        gevent.sleep(60)

def start_monitoring(app, stream):
    """Start monitoring for a specific stream."""
    with monitoring_lock:
        stream_url = get_stream_url(stream)
        if not stream_url:
            logger.error(f"No valid stream URL for stream ID {stream.id}")
            return False

        if stream_url in stream_processors:
            logger.info(f"Monitoring already active for {stream_url}")
            return True

        try:
            # Pre-load models in the main thread with app context
            with app.app_context():
                load_yolo_model(app)
                load_whisper_model(app)

            # Create greenlets with proper app context handling
            stream_greenlet = ContextGreenlet(app, monitor_stream, app, stream, stream_url)
            chat_greenlet = ContextGreenlet(app, monitor_chat_stream, app, stream, stream_url)

            stream_processors[stream_url] = {
                'stream': stream_greenlet,
                'chat': chat_greenlet,
                'stream_id': stream.id,
                'started_at': datetime.now()
            }

            # Start the greenlets
            stream_greenlet.start()
            chat_greenlet.start()

            monitoring_status[stream_url] = {
                'stream_id': stream.id,
                'status': 'running',
                'started_at': datetime.now().isoformat(),
                'last_updated': datetime.now().isoformat(),
                'error': None,
                'greenlets': {
                    'stream': 'started',
                    'chat': 'started'
                }
            }

            logger.info(f"Started monitoring for {stream_url} (Stream ID: {stream.id})")
            
            # Emit update with app context
            with app.app_context():
                emit_stream_update({
                    'id': stream.id,
                    'url': stream_url,
                    'status': 'monitoring',
                    'type': stream.type
                })
            return True

        except Exception as e:
            logger.error(f"Failed to start monitoring for {stream_url}: {e}")
            monitoring_status[stream_url] = {
                'stream_id': stream.id,
                'status': 'failed',
                'started_at': None,
                'last_updated': datetime.now().isoformat(),
                'error': str(e),
                'greenlets': {}
            }
            return False

def stop_monitoring(app, stream):
    """Stop monitoring for a specific stream."""
    with monitoring_lock:
        stream_url = get_stream_url(stream)
        if stream_url not in stream_processors:
            logger.info(f"No active monitoring for {stream_url}")
            return

        try:
            processor_info = stream_processors[stream_url]
            
            # Kill the greenlets
            for greenlet_type in ['stream', 'chat']:
                greenlet = processor_info.get(greenlet_type)
                if isinstance(greenlet, Greenlet):
                    greenlet.kill()
                    logger.info(f"Killed {greenlet_type} greenlet for {stream_url}")

            del stream_processors[stream_url]
            monitoring_status[stream_url] = {
                'stream_id': stream.id,
                'status': 'stopped',
                'started_at': None,
                'last_updated': datetime.now().isoformat(),
                'error': None,
                'greenlets': {}
            }

            # Update database with app context
            with app.app_context():
                stream.is_monitored = False
                db.session.commit()

            logger.info(f"Stopped monitoring for {stream_url} (Stream ID: {stream.id})")
            
            # Emit update with app context
            with app.app_context():
                emit_stream_update({
                    'id': stream.id,
                    'url': stream_url,
                    'status': 'stopped',
                    'type': stream.type
                })

        except Exception as e:
            logger.error(f"Error stopping monitoring for {stream_url}: {e}")
            monitoring_status[stream_url]['error'] = str(e)

def initialize_monitoring(app):
    """Initialize monitoring for all active streams."""
    with app.app_context():
        try:
            streams = Stream.query.filter_by(is_monitored=True).all()
            for stream in streams:
                if stream.status != 'offline':
                    start_monitoring(app, stream)
            logger.info(f"Initialized monitoring for {len(streams)} streams")
        except Exception as e:
            logger.error(f"Error initializing monitoring: {e}")

def get_monitoring_status():
    """Get the current monitoring status for all streams."""
    with monitoring_lock:
        status = {
            'total_streams': len(stream_processors),
            'active_streams': {},
            'stopped_streams': {},
            'failed_streams': {},
            'last_updated': datetime.now().isoformat()
        }
        
        for url, info in monitoring_status.items():
            greenlet_status = {}
            if url in stream_processors:
                processor_info = stream_processors[url]
                for gtype in ['stream', 'chat']:
                    greenlet = processor_info.get(gtype)
                    if isinstance(greenlet, Greenlet):
                        if greenlet.dead:
                            greenlet_status[gtype] = 'failed'
                        elif greenlet.ready():
                            greenlet_status[gtype] = 'completed'
                        else:
                            greenlet_status[gtype] = 'active'
                            
            category = (
                'active_streams' if info['status'] == 'running' else
                'stopped_streams' if info['status'] == 'stopped' else
                'failed_streams'
            )
            status[category][url] = {**info, 'greenlets': greenlet_status}
            
        return status

def restart_all_monitoring(app):
    """Restart monitoring for all active streams."""
    with monitoring_lock:
        try:
            # Stop all current monitoring
            for stream_url in list(stream_processors.keys()):
                with app.app_context():
                    stream = Stream.query.filter_by(room_url=stream_url).first()
                    if not stream:
                        # Try chaturbate streams
                        cb_stream = ChaturbateStream.query.filter_by(chaturbate_m3u8_url=stream_url).first()
                        if cb_stream:
                            stream = Stream.query.get(cb_stream.id)
                        else:
                            # Try stripchat streams
                            sc_stream = StripchatStream.query.filter_by(stripchat_m3u8_url=stream_url).first()
                            if sc_stream:
                                stream = Stream.query.get(sc_stream.id)
                    
                    if stream:
                        stop_monitoring(app, stream)

            # Reinitialize monitoring
            initialize_monitoring(app)
            logger.info("All monitoring restarted successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error restarting monitoring: {e}")
            return False

def refresh_and_monitor_streams(app, stream_ids):
    """Refresh and start monitoring for selected streams."""
    try:
        with app.app_context():
            streams = Stream.query.filter(Stream.id.in_(stream_ids)).all()
            for stream in streams:
                if stream.type == 'chaturbate':
                    from scraping import refresh_chaturbate_stream
                    new_url = refresh_chaturbate_stream(stream.streamer_username)
                    if new_url:
                        child_stream = ChaturbateStream.query.get(stream.id)
                        child_stream.chaturbate_m3u8_url = new_url
                elif stream.type == 'stripchat':
                    from scraping import refresh_stripchat_stream
                    new_url = refresh_stripchat_stream(stream.room_url)
                    if new_url:
                        child_stream = StripchatStream.query.get(stream.id)
                        child_stream.stripchat_m3u8_url = new_url

                db.session.commit()
                if stream.status != 'offline':
                    start_monitoring(app, stream)
                    
            logger.info(f"Refreshed and started monitoring for {len(streams)} streams")
            return True
            
    except Exception as e:
        logger.error(f"Error refreshing and monitoring streams: {e}")
        return False

def auto_start_monitoring_on_online(app, streamer_username=None):
    """Automatically start monitoring for a stream when it goes online."""
    if streamer_username is None:
        logger.error("auto_start_monitoring_on_online called without streamer_username")
        return False

    with app.app_context():
        try:
            stream = Stream.query.filter_by(streamer_username=streamer_username).first()
            if not stream:
                logger.error(f"No stream found for username: {streamer_username}")
                return False

            if stream.status != 'online':
                logger.info(f"Stream {streamer_username} is not online, skipping monitoring")
                return False

            if stream.is_monitored:
                logger.info(f"Stream {streamer_username} is already monitored")
                return True

            success = start_monitoring(app, stream)
            if success:
                logger.info(f"Auto-started monitoring for {streamer_username}")
            else:
                logger.error(f"Failed to auto-start monitoring for {streamer_username}")
            return success

        except Exception as e:
            logger.error(f"Error auto-starting monitoring for {streamer_username}: {e}")
            return False

def start_notification_monitor_greenlet(app):
    """Start the notification monitor as a greenlet"""
    notification_greenlet = ContextGreenlet(app, start_notification_monitor, app)
    notification_greenlet.start()
    return notification_greenlet