import requests
import time
import logging
from typing import Dict, Any, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import ssl
import urllib3

# Disable SSL warnings for self-signed certificates in development
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

class CommunicationService:
    """Service for handling inter-application communication."""
    
    def __init__(self, app=None):
        self.app = app
        self.session = None
        self._setup_session()
    
    def _setup_session(self):
        """Setup requests session with retry strategy."""
        self.session = requests.Session()
        
        # Setup retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Setup headers
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'JetCamStudio-Service/1.0'
        })
    
    def _make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make HTTP request with error handling."""
        # Set default timeout if not provided
        if 'timeout' not in kwargs:
            kwargs['timeout'] = 30
        
        # Disable SSL verification for development (use with caution in production)
        if 'verify' not in kwargs and url.startswith('https://localhost'):
            kwargs['verify'] = False
        
        try:
            logger.info(f"Making {method.upper()} request to {url}")
            response = self.session.request(method, url, **kwargs)
            logger.info(f"Response status: {response.status_code}")
            return response
        except requests.exceptions.Timeout:
            logger.error(f"Timeout occurred when connecting to {url}")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error when connecting to {url}: {str(e)}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error when connecting to {url}: {str(e)}")
            raise
    
    def post_to_monitor(self, endpoint: str, data: Dict[Any, Any]) -> Dict[Any, Any]:
        """Send POST request to monitor app."""
        if not self.app:
            raise RuntimeError("App context not available")
        
        monitor_url = self.app.config.get('MONITOR_APP_URL', 'https://localhost:5001')
        url = f"{monitor_url}{endpoint}"
        
        try:
            response = self._make_request('POST', url, json=data)
            if response.status_code in [200, 201, 409]:  # 409 for already running
                return response.json()
            else:
                logger.error(f"Monitor app returned status {response.status_code}: {response.text}")
                return {
                    "error": f"Monitor app error: {response.status_code}",
                    "details": response.text
                }
        except Exception as e:
            logger.error(f"Failed to communicate with monitor app: {str(e)}")
            return {
                "error": f"Communication failed: {str(e)}",
                "fallback": True
            }
    
    def get_from_monitor(self, endpoint: str) -> Dict[Any, Any]:
        """Send GET request to monitor app."""
        if not self.app:
            raise RuntimeError("App context not available")
        
        monitor_url = self.app.config.get('MONITOR_APP_URL', 'https://localhost:5001')
        url = f"{monitor_url}{endpoint}"
        
        try:
            response = self._make_request('GET', url)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Monitor app returned status {response.status_code}: {response.text}")
                return {
                    "error": f"Monitor app error: {response.status_code}",
                    "details": response.text
                }
        except Exception as e:
            logger.error(f"Failed to communicate with monitor app: {str(e)}")
            return {
                "error": f"Communication failed: {str(e)}",
                "fallback": True
            }
    
    def post_to_main(self, endpoint: str, data: Dict[Any, Any]) -> Dict[Any, Any]:
        """Send POST request to main app."""
        if not self.app:
            raise RuntimeError("App context not available")
        
        main_url = self.app.config.get('MAIN_APP_URL', 'https://localhost:5000')
        url = f"{main_url}{endpoint}"
        
        try:
            response = self._make_request('POST', url, json=data)
            if response.status_code in [200, 201]:
                return response.json()
            else:
                logger.error(f"Main app returned status {response.status_code}: {response.text}")
                return {
                    "error": f"Main app error: {response.status_code}",
                    "details": response.text
                }
        except Exception as e:
            logger.error(f"Failed to communicate with main app: {str(e)}")
            return {
                "error": f"Communication failed: {str(e)}",
                "fallback": True
            }

# Global instance
communication_service = CommunicationService()