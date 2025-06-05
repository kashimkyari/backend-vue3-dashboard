"""
Utility functions for handling SSL connections and requests with improved error handling
"""
import logging
import requests
import ssl
import urllib3
import random
from urllib3.util import ssl_
from requests.adapters import HTTPAdapter

# Disable insecure request warnings for cleaner logs when verify=False is used
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class TLSAdapter(HTTPAdapter):
    """Transport adapter that allows using different TLS versions."""
    
    def __init__(self, ssl_options=0, **kwargs):
        self.ssl_options = ssl_options
        super(TLSAdapter, self).__init__(**kwargs)
    
    def init_poolmanager(self, *args, **kwargs):
        context = ssl_.create_urllib3_context(
            cert_reqs=ssl.CERT_REQUIRED if kwargs.get('verify', True) else ssl.CERT_NONE,
            options=self.ssl_options
        )
        kwargs['ssl_context'] = context
        return super(TLSAdapter, self).init_poolmanager(*args, **kwargs)


def get_ssl_session(force_tls_version=None, verify=True):
    """
    Creates a requests session with configurable TLS settings
    
    Args:
        force_tls_version: Optional SSL/TLS version to force
                          None = Auto (default)
                          "TLSv1" = TLS 1.0
                          "TLSv1_1" = TLS 1.1
                          "TLSv1_2" = TLS 1.2
                          "TLSv1_3" = TLS 1.3 (if available)
        verify (bool): Whether to verify SSL certificates
    
    Returns:
        requests.Session: Configured session object
    """
    session = requests.Session()
    
    # Default adapter with standard configuration
    if force_tls_version is None:
        return session
    
    # Configure specific TLS version if requested
    if force_tls_version == "TLSv1":
        adapter = TLSAdapter(ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | 
                            ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_2 | ssl.OP_NO_TLSv1_3)
    elif force_tls_version == "TLSv1_1":
        adapter = TLSAdapter(ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | 
                            ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_2 | ssl.OP_NO_TLSv1_3)
    elif force_tls_version == "TLSv1_2":
        adapter = TLSAdapter(ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | 
                            ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_3)
    elif force_tls_version == "TLSv1_3":
        # Check if TLS 1.3 is available (Python 3.7+ with OpenSSL 1.1.1+)
        if hasattr(ssl, 'OP_NO_TLSv1_3'):
            adapter = TLSAdapter(ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | 
                                ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_2)
        else:
            logging.warning("TLS 1.3 requested but not available, using TLS 1.2")
            adapter = TLSAdapter(ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | 
                                ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1)
    else:
        raise ValueError(f"Unsupported TLS version: {force_tls_version}")
    
    session.mount('https://', adapter)
    return session


def safe_request(url, method='GET', params=None, data=None, json=None, headers=None, 
                 timeout=30, verify=True, force_tls_version=None, retry_different_tls=True,
                 max_retries=3):
    """
    Make an HTTP request with robust SSL error handling
    
    Args:
        url (str): URL to request
        method (str): HTTP method ('GET', 'POST', etc.)
        params (dict): Query parameters
        data (dict or str): Request body data
        json (dict): JSON data to send
        headers (dict): HTTP headers
        timeout (int): Request timeout in seconds
        verify (bool): Whether to verify SSL certificates
        force_tls_version (str): Force specific TLS version
        retry_different_tls (bool): If True, retry with different TLS versions on failure
        max_retries (int): Maximum number of retries per TLS version
    
    Returns:
        requests.Response: Response object or None if all attempts fail
    """
    # Default headers if none provided
    if headers is None:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
    
    # List of TLS versions to try if retry_different_tls is True
    tls_versions = [None, "TLSv1_2", "TLSv1_1", "TLSv1"]
    if force_tls_version:
        # If a specific version is requested, try that first
        if force_tls_version in tls_versions:
            tls_versions.remove(force_tls_version)
        tls_versions.insert(0, force_tls_version)
    
    errors = []
    
    # Try with different TLS versions
    for tls_version in (tls_versions if retry_different_tls else [force_tls_version or None]):
        # Try multiple times with the same TLS version
        for retry in range(max_retries):
            try:
                # Add jitter to avoid overwhelming the server
                if retry > 0:
                    jitter = random.uniform(0.1, 0.5) * retry
                    logging.debug(f"Retry {retry} for {url} with {tls_version or 'default'} TLS, waiting {jitter:.2f}s")
                    time.sleep(jitter)
                
                session = get_ssl_session(tls_version, verify=verify)
                response = session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=data,
                    json=json,
                    headers=headers,
                    timeout=timeout,
                    verify=verify
                )
                
                # Check if we got a valid response
                if response.status_code < 400:
                    return response
                else:
                    error_msg = f"HTTP error {response.status_code} with {tls_version or 'default'} TLS (retry {retry+1}/{max_retries})"
                    logging.debug(error_msg)
                    # Only add to errors on last retry
                    if retry == max_retries - 1:
                        errors.append(error_msg)
                    continue
                    
            except (requests.exceptions.SSLError, ssl.SSLError) as e:
                error_msg = f"SSL error with {tls_version or 'default'} TLS (retry {retry+1}/{max_retries}): {str(e)}"
                logging.debug(error_msg)
                # Only add to errors on last retry
                if retry == max_retries - 1:
                    errors.append(error_msg)
                continue
                
            except Exception as e:
                error_msg = f"Request error with {tls_version or 'default'} TLS (retry {retry+1}/{max_retries}): {str(e)}"
                logging.debug(error_msg)
                # Only add to errors on last retry
                if retry == max_retries - 1:
                    errors.append(error_msg)
                # For non-SSL errors, break retry loop but try next TLS version
                break
    
    # If we've tried all versions and none worked
    error_details = '\n'.join(errors)
    logging.error(f"Failed to make request to {url} after trying multiple TLS versions and retries:\n{error_details}")
    return None


def fetch_proxyscrape_proxies(timeout=30):
    """
    Fetch proxies from proxyscrape.com API with robust SSL handling
    
    Args:
        timeout (int): Request timeout in seconds
        
    Returns:
        list: List of proxies in format "ip:port" or empty list if request failed
    """
    # Try multiple proxy sources for redundancy
    proxy_sources = [
        # ProxyScrape API
        {
            "url": "https://api.proxyscrape.com/v2/",
            "params": {
                "request": "displayproxies",
                "protocol": "http",
                "timeout": 10000,
                "country": "all",
                "ssl": "all",
                "anonymity": "all"
            }
        },
        # Fallback to a more reliable proxy source
        {
            "url": "https://www.proxy-list.download/api/v1/get",
            "params": {
                "type": "http"
            }
        },
        # Another alternative source
        {
            "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            "params": {}
        }
    ]
    
    for source in proxy_sources:
        try:
            # First try with SSL verification
            response = safe_request(
                url=source["url"],
                params=source["params"],
                timeout=timeout,
                retry_different_tls=True,
                verify=True
            )
            
            # If that fails, try without SSL verification
            if not response:
                logging.warning(f"Trying {source['url']} without SSL verification")
                response = safe_request(
                    url=source["url"],
                    params=source["params"],
                    timeout=timeout,
                    retry_different_tls=True,
                    verify=False
                )
            
            if response and response.status_code == 200:
                # Parse the response content (typically IP:PORT per line)
                proxies = [line.strip() for line in response.text.splitlines() if line.strip() and ":" in line.strip()]
                if proxies:
                    logging.info(f"Successfully fetched {len(proxies)} proxies from {source['url']}")
                    return proxies
                else:
                    logging.warning(f"No valid proxies found at {source['url']}")
            else:
                status = response.status_code if response else "No response"
                logging.warning(f"Failed to fetch proxies from {source['url']}: HTTP {status}")
                
        except Exception as e:
            logging.warning(f"Exception while fetching proxies from {source['url']}: {str(e)}")
    
    # If all sources failed, log and return empty list
    logging.error("All proxy sources failed")
    return []