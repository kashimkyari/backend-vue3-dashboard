#!/usr/bin/env python3
"""
chaturbate_scraper_updated.py

This module provides scraping functions for Chaturbate and Stripchat streams.
The updated Chaturbate scraper uses a POST request to retrieve the HLS URL 
via free proxies and fetches room_uid and broadcaster_uid. SSL verification is disabled due to known proxy issues.
"""
# import gevent.monkey
# gevent.monkey.patch_all()  # Apply at the start of the application
from asyncio import as_completed
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import sys
import types
import tempfile
import os
import re
import logging
import uuid
import time
import random
import requests
import urllib3
import gevent
from gevent.pool import Pool
from gevent.lock import Semaphore

# Monkey Patch for blinker._saferef
if 'blinker._saferef' not in sys.modules:
    saferef = types.ModuleType('blinker._saferef')
    import weakref
    class SafeRef(weakref.ref):
        def __init__(self, ob, callback=None):
            super().__init__(ob, callback)
            self._hash = hash(ob)
        def __hash__(self):
            return self._hash
        def __eq__(self, other):
            try:
                return self() is other()
            except Exception:
                return False
    saferef.SafeRef = SafeRef
    sys.modules['blinker._saferef'] = saferef

from requests.exceptions import RequestException, SSLError
from urllib.parse import urlparse
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from flask import jsonify, current_app
from datetime import datetime
from services.assignment_service import AssignmentService
from services.notification_service import NotificationService
from models import Stream, ChaturbateStream, StripchatStream, Assignment, User
from extensions import db


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

scrape_jobs = {}
stream_creation_jobs = {}
gevent_pool = Pool(5)  # Max 5 workers
PROXY_LIST = []
PROXY_LIST_LAST_UPDATED = None
PROXY_LOCK = Semaphore()
PROXY_UPDATE_INTERVAL = 3600

def update_proxy_list():
    """Fetch fresh proxies from free API services"""
    try:
        response = requests.get(
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
            timeout=15
        )
        if response.status_code == 200 and response.text:
            proxies = [proxy.strip() for proxy in response.text.split('\n') if proxy.strip()]
            if len(proxies) > 20:
                return proxies
                
        response = requests.get(
            "https://www.proxy-list.download/api/v1/get?type=http",
            timeout=15
        )
        if response.status_code == 200 and response.text:
            proxies = [proxy.strip() for proxy in response.text.split('\n') if proxy.strip()]
            if len(proxies) > 20:
                return proxies
                
        return None
    except Exception as e:
        logging.error(f"Failed to update proxy list: {str(e)}")
        return None

def get_random_proxy():
    """Select a random proxy from the proxy list, refreshing if needed."""
    global PROXY_LIST, PROXY_LIST_LAST_UPDATED
    
    with PROXY_LOCK:
        current_time = time.time()
        if not PROXY_LIST or not PROXY_LIST_LAST_UPDATED or \
           current_time - PROXY_LIST_LAST_UPDATED > PROXY_UPDATE_INTERVAL:
            new_proxies = update_proxy_list() or get_proxies_with_library() or scrape_free_proxy_list()
            
            if new_proxies and len(new_proxies) >= 10:
                PROXY_LIST = new_proxies
                PROXY_LIST_LAST_UPDATED = current_time
                logging.info(f"Updated proxy list with {len(PROXY_LIST)} proxies")
            elif not PROXY_LIST:
                PROXY_LIST = [
                    "52.67.10.183:80",
                    "200.250.131.218:80",
                ]
                logging.warning("Using static proxy list as fallback")
    
    if PROXY_LIST:
        proxy = random.choice(PROXY_LIST)
        return {
            "http": f"http://{proxy}",
            "https": f"http://{proxy}"
        }
    return None

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
    max_attempts = 3
    attempts = 0
    while attempts < max_attempts:
        proxy_dict = get_random_proxy()
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
            logging.debug(f"Fetched Chaturbate UIDs for {streamer_username}: broadcaster_uid={broadcaster_uid}, room_uid={room_uid}")
            return broadcaster_uid, room_uid
        except Exception as e:
            attempts += 1
            logging.warning(f"Attempt {attempts} failed for Chaturbate room UID fetch for {streamer_username}: {e}")
            if attempts < max_attempts:
                time.sleep(1)
    logging.error(f"Failed to fetch Chaturbate room UID for {streamer_username} after {max_attempts} attempts")
    return None, None

def create_selenium_driver_with_proxy(headless=True):
    """Create a Selenium driver configured with a random proxy"""
    proxy_dict = get_random_proxy()
    proxy_address = None if not proxy_dict else proxy_dict["http"].replace("http://", "")
    
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    unique_user_data_dir = tempfile.mkdtemp()
    chrome_options.add_argument(f"--user-data-dir={unique_user_data_dir}")
    
    seleniumwire_options = {}
    if proxy_address:
        seleniumwire_options = {
            'proxy': {
                'http': f'http://{proxy_address}',
                'https': f'http://{proxy_address}',
                'verify_ssl': False,
            }
        }
    
    driver = webdriver.Chrome(
        options=chrome_options,
        seleniumwire_options=seleniumwire_options
    )
    
    return driver

def update_job_progress(job_id, percent, message):
    """Update the progress of a scraping job"""
    now = time.time()
    if job_id not in scrape_jobs or 'start_time' not in scrape_jobs[job_id]:
        scrape_jobs[job_id] = {'start_time': now}
    elapsed = now - scrape_jobs[job_id]['start_time']
    estimated = None
    if percent > 0:
        estimated = (100 - percent) / percent * elapsed
    scrape_jobs[job_id].update({
        "progress": percent,
        "message": message,
        "elapsed": round(elapsed, 1),
        "estimated_time": round(estimated, 1) if estimated is not None else None,
    })
    logging.info("Job %s progress: %s%% - %s (Elapsed: %ss, Est: %ss)",
                 job_id, percent, message,
                 scrape_jobs[job_id]['elapsed'],
                 scrape_jobs[job_id]['estimated_time'])

def update_stream_job_progress(job_id, percent, message, estimated_time=None):
    """Update job progress with safe initialization."""
    now = time.time()
    job = stream_creation_jobs.setdefault(job_id, {
        'start_time': now,
        'progress': 0,
        'message': '',
        'estimated_time': 0,
        'last_updated': now,
        'error': None,
        'stream': None,
        'assignment': None,
    })

    elapsed = now - job['start_time']
    if percent > 0 and percent < 100:
        estimated_total = elapsed / (percent / 100)
        estimated_remaining = max(0, int(estimated_total - elapsed))
    else:
        estimated_remaining = 0

    final_estimated_time = estimated_time if estimated_time is not None else estimated_remaining

    if (abs(percent - job['progress']) > 1 or
        message != job['message'] or
        percent == 100 or
        job['error'] is not None):
        
        job.update({
            'progress': min(100, max(0, percent)),
            'message': message,
            'estimated_time': final_estimated_time,
            'last_updated': now,
        })
        
        logging.info("Stream Job %s: %s%% - %s (Est: %ss)",
                    job_id, percent, message, final_estimated_time)

def extract_room_slug(url):
    """Extract the room slug from a Chaturbate URL"""
    parsed_url = urlparse(url)
    path_parts = [part for part in parsed_url.path.split('/') if part]
    if not path_parts:
        raise ValueError("No room slug found in URL")
    return path_parts[0]

def get_proxies_with_library(count=50):
    """Get working proxies using free-proxy library"""
    try:
        from fp.fp import FreeProxy
        proxies = []
        for _ in range(count):
            try:
                proxy = FreeProxy(timeout=1, https=True).get()
                if proxy:
                    proxy = proxy.replace("http://", "").replace("https://", "")
                    proxies.append(proxy)
            except Exception:
                continue
        return proxies if proxies else None
    except ImportError:
        logging.error("free-proxy library not installed. Run: pip install free-proxy")
        return None
    except Exception as e:
        logging.error(f"Error getting proxies with library: {str(e)}")
        return None

def scrape_free_proxy_list():
    """Scrape proxies from free-proxy-list.net"""
    try:
        url = "https://free-proxy-list.net/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')
        proxy_table = soup.find('table', {'id': 'proxylisttable'})
        proxies = []
        if proxy_table:
            for row in proxy_table.find('tbody').find_all('tr'):
                cols = row.find_all('td')
                if len(cols) >= 2:
                    ip = cols[0].text.strip()
                    port = cols[1].text.strip()
                    proxies.append(f"{ip}:{port}")
        return proxies if proxies else None
    except Exception as e:
        logging.error(f"Failed to scrape free-proxy-list.net: {str(e)}")
        return None

def get_hls_url(room_slug, max_attempts=15):
    """Send a POST request to Chaturbate's endpoint to fetch the HLS URL"""
    url = 'https://chaturbate.com/get_edge_hls_url_ajax/'
    boundary = uuid.uuid4().hex
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': f'https://chaturbate.com/{room_slug}/',
        'Origin': 'https://chaturbate.com',
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Cookie': (
            'csrftoken=QBEfLYOhYb02QMAA8FsDhvimMi2rbhTh; '
            '__cf_bm=aRWJoCGvyxZsRyCS9qMJeMwF1ikmvIEucwTQpB3VDcE-1743303491-1.0.1.1-pc6j_3W8_POkMuCh2yhnhdG18vOaMl1tAsv9bIjj8wDQn9M4W3pGJN5yaucI8_vJp4meSVffE62zILQmuHg.ipapPlEw3OCsfsBNg05dEV0; '
            'sbr=sec:sbr9f095e3f-07ec-4e77-a51a-051c8118632f:1txykY:nZcRPVNiTcLgruuwAyCND2URhh7k8KiarIG-keMrJm0; '
            'agreeterms=1; '
            'stcki="Eg6Gdq=1"'
        )
    }

    payload = (
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="room_slug"\r\n\r\n'
        f'{room_slug}\r\n'
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="bandwidth"\r\n\r\n'
        'high\r\n'
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="current_edge"\r\n\r\n'
        '\r\n'
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="exclude_edge"\r\n\r\n'
        '\r\n'
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="csrfmiddlewaretoken"\r\n\r\n'
        'QBEfLYOhYb02QMAA8FsDhvimMi2rbhTh\r\n'
        f'--{boundary}--\r\n'
    )

    attempts = 0
    while attempts < max_attempts:
        proxy_dict = get_random_proxy()
        try:
            response = requests.post(
                url,
                headers=headers,
                data=payload.encode('utf-8'),
                proxies=proxy_dict,
                timeout=10,
                verify=False
            )
            response.raise_for_status()
            result = response.json()

            if result.get('room_status') == 'offline':
                return {'error': 'room_offline', 'message': 'Stream is offline'}

            hls_url = result.get("hls_url") or result.get("url")
            if hls_url:
                result["hls_url"] = hls_url
                return result
            
            attempts += 1
        except Exception as e:
            attempts += 1
            gevent.sleep(1)

    return None

def fetch_chaturbate_hls_with_curl_method(room_slug):
    """Direct implementation of the curl-based approach to fetch HLS URL"""
    url = 'https://chaturbate.com/get_edge_hls_url_ajax/'
    boundary = f'geckoformboundary{uuid.uuid4().hex[:24]}'
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0',
        'Accept': '*/*',
        'Referer': f'https://chaturbate.com/{room_slug}/',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin': 'https://chaturbate.com',
        'Content-Type': f'multipart/form-data; boundary=----{boundary}',
        'Cookie': 'csrftoken=ZF2KoQPEfT3ikgEEvhx4Ht4Dfg9LOo3f; stcki="Eg6Gdq=1"; agreeterms=1;'
    }
    
    payload = (
        f'------{boundary}\r\n'
        f'Content-Disposition: form-data; name="room_slug"\r\n\r\n'
        f'{room_slug}\r\n'
        f'------{boundary}\r\n'
        f'Content-Disposition: form-data; name="bandwidth"\r\n\r\n'
        'high\r\n'
        f'------{boundary}\r\n'
        f'Content-Disposition: form-data; name="current_edge"\r\n\r\n'
        'edge20-mad.live.mmcdn.com\r\n'
        f'------{boundary}\r\n'
        f'Content-Disposition: form-data; name="exclude_edge"\r\n\r\n'
        '\r\n'
        f'------{boundary}\r\n'
        'Content-Disposition: form-data; name="csrfmiddlewaretoken"\r\n\r\n'
        'ZF2KoQPEfT3ikgEEvhx4Ht4Dfg9LOo3f\r\n'
        f'------{boundary}--\r\n'
    )
    
    try:
        proxy_dict = get_random_proxy()
        response = requests.post(
            url,
            headers=headers,
            data=payload.encode('utf-8'),
            proxies=proxy_dict,
            timeout=10,
            verify=False
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get('room_status') == 'offline':
            return {'error': 'room_offline', 'message': 'Stream is offline'}
        
        hls_url = result.get("hls_url") or result.get("url")
        if hls_url:
            result["hls_url"] = hls_url
            return result
        
        return None
    except Exception as e:
        logging.error(f"Curl-method request failed: {str(e)}")
        return None

def scrape_chaturbate_data(url, progress_callback=None):
    """Enhanced Chaturbate scraper that searches for any .m3u8 URL in XHR network requests and fetches room_uid and broadcaster_uid"""
    try:
        if not url or 'chaturbate.com/' not in url:
            raise ValueError("Invalid Chaturbate URL")

        def update_progress(p, m):
            if progress_callback:
                progress_callback(p, m)

        update_progress(10, "Extracting room slug")
        room_slug = extract_room_slug(url)

        update_progress(20, "Fetching room and broadcaster UIDs")
        broadcaster_uid, room_uid = fetch_chaturbate_room_uid(room_slug)
        if not broadcaster_uid or not room_uid:
            logging.warning(f"Could not fetch UIDs for {room_slug}, proceeding with scraping")

        update_progress(30, "Fetching HLS URL via API")
        result = get_hls_url(room_slug)
        
        if not result or 'error' in result or not result.get('hls_url'):
            update_progress(35, "Primary method failed, trying curl-based fallback")
            result = fetch_chaturbate_hls_with_curl_method(room_slug)
        
        if not result:
            raise ValueError("Empty response from Chaturbate API")
        if 'error' in result:
            error_msg = result.get('message', 'Unknown error')
            raise RuntimeError(f"Chaturbate API error: {error_msg}")

        hls_url = result.get("hls_url") or result.get("url")
        if not hls_url or ".m3u8" not in hls_url:
            update_progress(40, "API methods failed, falling back to browser scraping")
            hls_url = None
        else:
            update_progress(100, "Scraping complete")
            return {
                "status": "online",
                "streamer_username": room_slug,
                "chaturbate_m3u8_url": hls_url,
                "broadcaster_uid": broadcaster_uid,
                "room_uid": room_uid,
            }

        update_progress(50, "Searching XHR requests for .m3u8 URL")
        driver = create_selenium_driver_with_proxy(headless=True)
        
        try:
            driver.scopes = [r'.*\.m3u8.*']
            driver.get(url)
            found_url = None
            timeout = 15
            start_time = time.time()

            while time.time() - start_time < timeout:
                elapsed = time.time() - start_time
                progress_percent = 50 + int((elapsed / timeout) * 30)
                update_progress(progress_percent, f"Waiting for stream URL... {int(elapsed)}s elapsed")
                for request in driver.requests:
                    if request.response and ".m3u8" in request.url:
                        found_url = request.url.split('?')[0]
                        break
                if found_url:
                    break
                gevent.sleep(1)

            if found_url:
                hls_url = found_url
            else:
                raise RuntimeError("M3U8 URL not found in network requests")
        finally:
            driver.quit()

        if not re.match(r"https?://[^\s]+\.m3u8", hls_url):
            raise ValueError("Invalid HLS URL format detected")

        update_progress(100, "Scraping complete")
        return {
            "status": "online",
            "streamer_username": room_slug,
            "chaturbate_m3u8_url": hls_url,
            "broadcaster_uid": broadcaster_uid,
            "room_uid": room_uid,
        }

    except Exception as e:
        error_msg = f"Scraping failed: {str(e)}"
        logging.error(error_msg)
        if progress_callback:
            progress_callback(100, error_msg)
        return {
            "status": "error",
            "message": error_msg,
            "details": str(e)
        }

def fetch_page_content(url, use_selenium=False):
    """Fetch the HTML content of the provided URL."""
    if use_selenium:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--ignore-certificate-errors")
        unique_user_data_dir = tempfile.mkdtemp()
        chrome_options.add_argument(f"--user-data-dir={unique_user_data_dir}")
        driver = webdriver.Chrome(options=chrome_options)
        try:
            driver.get(url)
            gevent.sleep(5)
            return driver.page_source
        finally:
            driver.quit()
    else:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:112.0) "
                "Gecko/20100101 Firefox/112.0"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://chaturbate.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        session = requests.Session()
        try:
            response = session.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logging.error("Direct request failed: %s. Trying Selenium...", e)
            return fetch_page_content(url, use_selenium=True)

def extract_m3u8_urls(html_content):
    """Extract m3u8 URLs from the given HTML content."""
    pattern = r'https?://[^\s"\']+\.m3u8'
    urls = re.findall(pattern, html_content)
    return urls

def fetch_m3u8_from_page(url, timeout=90):
    """Fetch the M3U8 URL from the given page using Selenium."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--ignore-certificate-errors")
    unique_user_data_dir = tempfile.mkdtemp()
    chrome_options.add_argument(f"--user-data-dir={unique_user_data_dir}")

    driver = webdriver.Chrome(options=chrome_options)
    driver.scopes = ['.*\\.m3u8']

    try:
        logging.info(f"Opening URL: {url}")
        driver.get(url)
        gevent.sleep(5)
        found_url = None
        elapsed = 0
        while elapsed < timeout:
            for request in driver.requests:
                if request.response and ".m3u8" in request.url:
                    found_url = request.url
                    logging.info(f"Found M3U8 URL: {found_url}")
                    break
            if found_url:
                break
            gevent.sleep(1)
            elapsed += 1
        return found_url if found_url else None
    except Exception as e:
        logging.error(f"Error fetching M3U8 URL: {e}")
        return None
    finally:
        driver.quit()

def scrape_stripchat_data(url, progress_callback=None):
    """Enhanced Stripchat scraper combining network interception with direct JavaScript"""
    def update_progress(percent, message):
        if progress_callback:
            progress_callback(percent, message)

    try:
        update_progress(15, "Initializing browser")

        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/119.0.0.0 Safari/537.36")

        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        driver = webdriver.Chrome(options=chrome_options)
        driver.execute_cdp_cmd("Network.enable", {})

        try:
            update_progress(20, "Loading page")
            driver.get(url)
            
            update_progress(30, "Inspecting player state")
            hls_url = None
            js_script = """
            var player = Array.from(document.querySelectorAll('video'))
                .find(v => v.__vue__ && v.__vue__.$player);
            if (player) {
                return player.__vue__.$player._lastKnownStreamConfig?.hlsStreamUrl || 
                    (player.__vue__.$player._playerInstance?.hls?.url);
            }
            return null;
            """
            
            try:
                hls_url = driver.execute_script(js_script)
                if hls_url and "m3u8" in hls_url:
                    logging.debug(f"Direct HLS URL from player state: {hls_url}")
            except Exception as js_error:
                logging.warning(f"JS player inspection failed: {js_error}")

            if not hls_url:
                update_progress(43, "Monitoring network requests")
                m3u8_urls = set()
                start_time = time.time()
                max_wait = 60
                
                while time.time() - start_time < max_wait:
                    for request in driver.requests:
                        if (request.response and 
                            request.method == "GET" and 
                            "m3u8" in request.url and 
                            "segment" not in request.url):
                            clean_url = request.url.split('?')[0]
                            m3u8_urls.add(clean_url)
                    if m3u8_urls:
                        break
                    gevent.sleep(1)
                    update_progress(43 + ((time.time() - start_time) / max_wait) * 10, "Still searching for stream...")

                hls_url = next((url for url in m3u8_urls 
                              if any(kw in url for kw in ['chunklist', 'index'])), None)
                if not hls_url and m3u8_urls:
                    hls_url = next(iter(m3u8_urls))

            if not hls_url:
                raise RuntimeError("HLS URL not found through any method")

            update_progress(69, "Validating stream configuration")
            if not re.match(r"https?://[^\s]+\.m3u8", hls_url):
                raise ValueError("Invalid HLS URL format")

            metadata_script = """
            return {
                resolutions: window.__NUXT__?.data?.player?.data?.resolutions,
                isLive: window.__NUXT__?.data?.player?.data?.isLive,
                broadcaster: window.__NUXT__?.data?.player?.data?.username
            }
            """
            metadata = driver.execute_script(metadata_script) or {}
            
            update_progress(100, "Stream data captured successfully")
            return {
                "status": "online",
                "streamer_username": metadata.get("broadcaster") or url.split("/")[-1],
                "stripchat_m3u8_url": hls_url,
                "resolutions": metadata.get("resolutions", []),
                "is_live": metadata.get("isLive", True),
                "detection_method": "player_state" if hls_url else "network_interception"
            }

        finally:
            driver.quit()

    except Exception as e:
        error_msg = f"Scraping error: {str(e)}"
        logging.error(error_msg)
        update_progress(100, error_msg)
        return {
            "status": "error",
            "message": str(e),
            "error_type": "scraping_error",
            "platform": "stripchat"
        }

def run_scrape_job(job_id, url):
    """Run a scraping job and update progress interactively."""
    update_job_progress(job_id, 0, "Starting scrape job")
    if "chaturbate.com" in url:
        result = scrape_chaturbate_data(url, progress_callback=lambda p, m: update_job_progress(job_id, p, m))
    elif "stripchat.com" in url:
        result = scrape_stripchat_data(url, progress_callback=lambda p, m: update_job_progress(job_id, p, m))
    else:
        logging.error("Unsupported platform for URL: %s", url)
        result = None
    if result:
        scrape_jobs[job_id]["result"] = result
    else:
        scrape_jobs[job_id]["error"] = "Scraping failed"
    update_job_progress(job_id, 100, scrape_jobs[job_id].get("error", "Scraping complete"))

def run_stream_creation_job(app, job_id, room_url, platform, agent_id=None, notes=None, priority='normal'):
    with app.app_context():
        start_time = time.time()
        stream_creation_jobs[job_id] = {
            'start_time': start_time,
            'progress': 0,
            'message': 'Initializing quantum flux capacitors',
            'estimated_time': 120,
            'last_updated': start_time,
            'error': None,
            'stream': None,
            'assignment': None,
            'created_at': datetime.now().isoformat(),
        }

        progress_markers = {
            'validation': {'start': 0, 'end': 10, 'microsteps': 5},
            'scraping': {'start': 10, 'end': 55, 'microsteps': 12},
            'database': {'start': 55, 'end': 75, 'microsteps': 8},
            'assignment': {'start': 75, 'end': 90, 'microsteps': 6},
            'finalization': {'start': 90, 'end': 100, 'microsteps': 5},
        }

        phase_messages = {
            'validation': [
                "Initializing neural pathways",
                "Verifying dimensional integrity",
                "Checking stream paradox coefficients",
                "Validating URL quantum state",
                "Confirming reality alignment",
            ],
            'scraping': [
                f"Deploying reconnaissance nanobots to {platform}",
                "Executing stealth protocol alpha",
                "Decrypting stream topology",
                "Establishing subspace connection",
                "Bypassing anti-scraping shields",
                "Extracting stream data packets",
                "Compressing hyperdata",
                "Decoding stream metadata",
                "Analyzing transmission integrity",
                "Computing bandwidth prerequisites",
                "Validating data fidelity",
                "Finalizing stream parameters",
            ],
            'database': [
                "Warming up the database hyperdrive",
                "Constructing data architecture",
                "Initializing transaction wormhole",
                "Aligning quantum database indices",
                "Optimizing data insertion vectors",
                "Establishing persistence field",
                "Committing to spacetime continuum",
                "Synchronizing parallel universes",
            ],
            'assignment': [
                "Locating agent in the multiverse",
                "Verifying agent clearance level",
                "Establishing secure neural link",
                "Creating agent-stream quantum entanglement",
                "Configuring assignment algorithms",
                "Recording assignment in universal ledger",
            ],
            'finalization': [
                "Engaging notification hyperdrive",
                "Broadcasting across all dimensions",
                "Notifying the Telegram Council",
                "Integrating with cosmic mesh network",
                "Completing stream initialization",
            ],
        }

        last_progress = 0
        last_micro_update = time.time() - 2
        micro_interval = 0.7

        def update_with_phase(phase, subprogress=0, custom_message=None):
            nonlocal last_progress, last_micro_update
            if phase not in progress_markers:
                return
            markers = progress_markers[phase]
            phase_progress = markers['start'] + (markers['end'] - markers['start']) * (subprogress / 100)
            phase_progress = max(int(phase_progress), last_progress)
            current_time = time.time()
            if custom_message is None and current_time - last_micro_update >= micro_interval:
                micro_step = min(int(subprogress / (100 / len(phase_messages[phase]))), len(phase_messages[phase]) - 1)
                phase_message = phase_messages[phase][micro_step]
                last_micro_update = current_time
            else:
                phase_message = custom_message or f"Processing {phase}"
            elapsed = current_time - start_time
            progress_delta = phase_progress - last_progress
            if progress_delta > 0:
                estimated_total = elapsed * (100 / max(phase_progress, 1)) * 0.9
                remaining = max(estimated_total - elapsed, 0)
                update_stream_job_progress(
                    job_id, phase_progress, phase_message, estimated_time=int(remaining)
                )
                last_progress = phase_progress

        try:
            for i in range(progress_markers['validation']['microsteps']):
                progress_pct = (i / progress_markers['validation']['microsteps']) * 100
                update_with_phase('validation', progress_pct)
                gevent.sleep(0.2)

            update_with_phase('scraping', 5, f"Deploying data extraction probes to {platform}")
            try:
                def scraping_progress_callback(percent, message):
                    jitter = random.uniform(-2, 2)
                    adj_percent = max(0, min(100, percent + jitter))
                    update_with_phase('scraping', adj_percent, message)

                max_retries = 3
                retry_count = 0
                scraped_data = None
                while retry_count < max_retries:
                    try:
                        if platform == "chaturbate":
                            scraped_data = scrape_chaturbate_data(room_url, scraping_progress_callback)
                        else:
                            scraped_data = scrape_stripchat_data(room_url, scraping_progress_callback)
                        if scraped_data and 'status' in scraped_data and scraped_data['status'] == 'online':
                            break
                        retry_count += 1
                        if retry_count >= max_retries:
                            raise RuntimeError("Scraping failed after maximum retries")
                        retry_delay = 2 * retry_count
                        update_with_phase('scraping', 40 + retry_count * 10, f"Retrying scraping (attempt {retry_count+1}/{max_retries})")
                        gevent.sleep(retry_delay)
                    except Exception as e:
                        retry_count += 1
                        if retry_count >= max_retries:
                            raise
                        retry_delay = 2 * retry_count
                        update_with_phase('scraping', 40 + retry_count * 10, f"Retrying scraping (attempt {retry_count+1}/{max_retries})")
                        gevent.sleep(retry_delay)

                update_with_phase('scraping', 85, "Verifying data integrity")
                if not scraped_data or 'status' not in scraped_data:
                    raise RuntimeError("Invalid scraping response")
                update_with_phase('scraping', 90, "Analyzing stream quantum state")
                if scraped_data['status'] != 'online':
                    raise RuntimeError(scraped_data.get('message', 'Stream is offline'))
                update_with_phase('scraping', 95, "Confirming hyperlink stability")
                expected_key = f"{platform}_m3u8_url"
                if not scraped_data.get(expected_key):
                    raise RuntimeError(f"Missing stream URL")
                update_with_phase('scraping', 100, "Stream data extracted")
            except Exception as e:
                update_with_phase('scraping', 100, f"Scraping failed: {str(e)}")
                raise

            update_with_phase('database', 10, "Preparing database quantum entanglement")
            try:
                with db.session.begin():
                    existing_stream = db.session.query(Stream).filter_by(room_url=room_url).with_for_update().first()
                    if existing_stream:
                        raise ValueError(f"Stream already exists with URL: {room_url}")
                    if platform == "chaturbate":
                        stream = ChaturbateStream(
                            room_url=room_url,
                            streamer_username=scraped_data['streamer_username'],
                            chaturbate_m3u8_url=scraped_data['chaturbate_m3u8_url'],
                            broadcaster_uid=scraped_data.get('broadcaster_uid'),
                            type='chaturbate',
                        )
                    else:
                        stream = StripchatStream(
                            room_url=room_url,
                            streamer_username=scraped_data['streamer_username'],
                            stripchat_m3u8_url=scraped_data['stripchat_m3u8_url'],
                            type='stripchat',
                        )
                    db.session.add(stream)
                    db.session.flush()
                db.session.refresh(stream)
                update_with_phase('database', 100, "Stream record materialized")
            except Exception as e:
                update_with_phase('database', 100, f"Database error: {str(e)}")
                raise

            update_with_phase('assignment', 20, "Establishing agent neural connection")
            assignment = None
            try:
                if agent_id:
                    assignment, created = AssignmentService.assign_stream_to_agent(
                        stream_id=stream.id,
                        agent_id=agent_id,
                        assigner_id=None,
                        notes=notes,
                        priority=priority,
                        metadata={"source": "interactive_creation"},
                    )
                else:
                    assignment, created = AssignmentService.auto_assign_stream(
                        stream_id=stream.id,
                        assigner_id=None,
                    )
                update_with_phase('assignment', 100, "Assignment completed")
            except Exception as e:
                update_with_phase('assignment', 100, f"Assignment failed: {str(e)}")
                logging.error(f"Assignment failed but stream created: {str(e)}")

            update_with_phase('finalization', 30, "Charging notification particle accelerator")
            try:
                # Notify admins
                NotificationService.notify_admins(
                    'stream_created',
                    {
                        'message': f"New stream created: {stream.streamer_username}",
                        'room_url': room_url,
                        'streamer_username': stream.streamer_username,
                        'platform': platform,
                        'assignment_id': assignment.id if assignment else None,
                    },
                    room_url,
                    platform,
                    stream.streamer_username,
                )
                
                # Notify assigned agent if exists
                if assignment and assignment.agent_id:
                    agent = User.query.get(assignment.agent_id)
                    if agent:
                        NotificationService.send_user_notification(
                            agent,
                            'stream_assigned',
                            {
                                'message': f"You have been assigned to stream: {stream.streamer_username}",
                                'room_url': room_url,
                                'streamer_username': stream.streamer_username,
                                'platform': platform,
                                'assignment_id': assignment.id,
                            },
                            room_url,
                            platform,
                            stream.streamer_username,
                        )
                
                update_with_phase('finalization', 95, "Notifications broadcasted")
            except Exception as e:
                logging.error(f"Notifications failed: {str(e)}")
                update_with_phase('finalization', 95, "Notification transmission failed")

            stream_creation_jobs[job_id].update({
                'progress': 100,
                'message': "Stream successfully created",
                'stream': stream.serialize(),
                'assignment': assignment.serialize() if assignment else None,
                'estimated_time': 0,
            })

        except Exception as e:
            error_msg = f"Creation failed: {str(e)}"
            logging.error(f"Stream creation job failed: {error_msg}")
            stream_creation_jobs[job_id].update({
                'error': error_msg,
                'progress': 100,
                'message': f"Mission aborted: {error_msg}",
            })
        finally:
            try:
                db.session.close()
            except Exception as e:
                logging.warning(f"Session close failed: {str(e)}")
            completion_time = time.time() - start_time
            logging.info(f"Stream creation job {job_id} completed in {completion_time:.2f} seconds")

def send_telegram_notifications(platform, streamer, room_url):
    """Robust notification handler"""
    try:
        recipients = User.query.filter(User.telegram_chat_id.isnot(None)).all()
        if not recipients:
            return

        message = (
            f"New Stream: {streamer}\n"
            f"Platform: {platform}\n"
            f"URL: {room_url}"
        )
        
        for recipient in recipients:
            gevent_pool.spawn(
                send_telegram_notifications,
                message=message,
                chat_id=recipient.telegram_chat_id
            )
                
    except Exception as e:
        logging.error("Notification system error: %s", str(e))

def fetch_chaturbate_chat_history(room_slug):
    """Fetch chat history from Chaturbate's API endpoint."""
    url = "https://chaturbate.com/push_service/room_history/"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://chaturbate.com/{room_slug}/",
        "Origin": "https://chaturbate.com",
        "Cookie": 'csrftoken=vfO2sk8hUsSXVILMJwtcyGqhPy6WqwhH; stcki="Eg6Gdq=1,kHDa2i=1"'
    }
    
    try:
        response = requests.post(url, headers=headers)
        response.raise_for_status()
        return response.json().get("0", {}).values()
    except Exception as e:
        logging.error(f"Chat history fetch error: {str(e)}")
        return []

def fetch_stripchat_chat_history(url):
    """Placeholder for fetching Stripchat chat history."""
    try:
        return []
    except Exception as e:
        logging.error(f"Stripchat chat fetch error: {str(e)}")
        return []

def refresh_chaturbate_stream(room_slug):
    """Refresh the m3u8 URL for a Chaturbate stream."""
    try:
        room_url = f"https://chaturbate.com/{room_slug}/"
        scraped_data = scrape_chaturbate_data(room_url)
        
        if not scraped_data or scraped_data.get('status') != 'online':
            logging.error("Scraping failed or stream offline for %s", room_slug)
            return None
        
        new_url = scraped_data.get('chaturbate_m3u8_url')
        if not new_url:
            logging.error("No valid m3u8 URL found for room slug: %s", room_slug)
            return None
        
        stream = ChaturbateStream.query.filter_by(streamer_username=room_slug).first()
        if stream:
            stream.chaturbate_m3u8_url = new_url
            stream.broadcaster_uid = scraped_data.get('broadcaster_uid')
            db.session.commit()
            logging.info("Updated stream '%s' with new m3u8 URL: %s, broadcaster_uid: %s", room_slug, new_url, stream.broadcaster_uid)
        else:
            logging.info("No existing stream found for %s, creating new", room_slug)
            stream = ChaturbateStream(
                room_url=room_url,
                streamer_username=room_slug,
                chaturbate_m3u8_url=new_url,
                broadcaster_uid=scraped_data.get('broadcaster_uid'),
                type='chaturbate'
            )
            db.session.add(stream)
            db.session.commit()
            logging.info("Created new stream for %s with m3u8 URL: %s, broadcaster_uid: %s", room_slug, new_url, stream.broadcaster_uid)

        # Notify admins and assigned agents
        NotificationService.notify_admins(
            'stream_refreshed',
            {
                'message': f"Chaturbate stream {room_slug} refreshed",
                'room_url': room_url,
                'streamer_username': room_slug,
                'platform': 'chaturbate',
                'new_url': new_url,
                'broadcaster_uid': scraped_data.get('broadcaster_uid'),
            },
            room_url,
            'chaturbate',
            room_slug,
        )
        
        assignment = Assignment.query.filter_by(stream_id=stream.id, status='active').first()
        if assignment and assignment.agent_id:
            agent = User.query.get(assignment.agent_id)
            if agent:
                NotificationService.send_user_notification(
                    agent,
                    'stream_refreshed',
                    {
                        'message': f"Stream {room_slug} refreshed",
                        'room_url': room_url,
                        'streamer_username': room_slug,
                        'platform': 'chaturbate',
                        'new_url': new_url,
                        'broadcaster_uid': scraped_data.get('broadcaster_uid'),
                    },
                    room_url,
                    'chaturbate',
                    room_slug,
                )

        return new_url
    except Exception as e:
        logging.error(f"Failed to refresh Chaturbate stream for %s: %s", room_slug, str(e))
        return None

def refresh_stripchat_stream(room_url):
    """Refresh the m3u8 URL for a Stripchat stream."""
    try:
        scraped_data = scrape_stripchat_data(room_url)
        
        if not scraped_data or scraped_data.get('status') != 'online':
            logging.error("Scraping failed or stream offline for %s", room_url)
            return None
        
        new_url = scraped_data.get('stripchat_m3u8_url')
        if not new_url:
            logging.error("No valid m3u8 URL found for URL: %s", room_url)
            return None
        
        stream = StripchatStream.query.filter_by(room_url=room_url).first()
        if stream:
            stream.stripchat_m3u8_url = new_url
            db.session.commit()
            logging.info("Updated stream at %s with new m3u8 URL: %s", room_url, new_url)
        else:
            logging.info("No existing stream found for %s, creating new", room_url)
            stream = StripchatStream(
                room_url=room_url,
                streamer_username=scraped_data.get('streamer_username', 'unknown'),
                stripchat_m3u8_url=new_url,
                type='stripchat'
            )
            db.session.add(stream)
            db.session.commit()
            logging.info("Created new stream for %s with m3u8 URL: %s", room_url, new_url)

        # Notify admins and assigned agents
        streamer_username = scraped_data.get('streamer_username', 'unknown')
        NotificationService.notify_admins(
            'stream_refreshed',
            {
                'message': f"Stripchat stream {streamer_username} refreshed",
                'room_url': room_url,
                'streamer_username': streamer_username,
                'platform': 'stripchat',
                'new_url': new_url,
            },
            room_url,
            'stripchat',
            streamer_username,
        )
        
        assignment = Assignment.query.filter_by(stream_id=stream.id, status='active').first()
        if assignment and assignment.agent_id:
            agent = User.query.get(assignment.agent_id)
            if agent:
                NotificationService.send_user_notification(
                    agent,
                    'stream_refreshed',
                    {
                        'message': f"Stream {streamer_username} refreshed",
                        'room_url': room_url,
                        'streamer_username': streamer_username,
                        'platform': 'stripchat',
                        'new_url': new_url,
                    },
                    room_url,
                    'stripchat',
                    streamer_username,
                )

        return new_url
    except Exception as e:
        logging.error(f"Failed to refresh Stripchat stream for %s: %s", room_url, str(e))
        return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("This module is intended to be imported, not run directly.")