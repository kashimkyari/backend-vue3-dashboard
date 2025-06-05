"""
Proxy handler module for managing and rotating proxies
"""
import logging
import time
import os
import random
from datetime import datetime, timedelta
from ssl_utils import fetch_proxyscrape_proxies
import requests

class ProxyManager:
    """Manages a pool of proxies with automatic rotation and refresh"""
    
    def __init__(self, 
                cache_file='proxies.txt',
                refresh_interval_hours=4,
                min_proxies=10):
        """
        Initialize the proxy manager
        
        Args:
            cache_file (str): Path to file for caching proxies
            refresh_interval_hours (int): Hours between proxy list refreshes
            min_proxies (int): Minimum number of proxies required
        """
        self.cache_file = cache_file
        self.refresh_interval = timedelta(hours=refresh_interval_hours)
        self.min_proxies = min_proxies
        self.proxies = []
        self.last_refresh = None
        
        # Initial proxy load
        self._load_proxies()
    
    def _load_proxies(self):
        """Load proxies from cache file or fetch new ones if needed"""
        try:
            # Check if cache file exists and is recent
            if os.path.exists(self.cache_file):
                file_mtime = datetime.fromtimestamp(os.path.getmtime(self.cache_file))
                file_age = datetime.now() - file_mtime
                
                # Load from cache if it's fresh enough
                if file_age < self.refresh_interval:
                    with open(self.cache_file, 'r') as f:
                        self.proxies = [line.strip() for line in f if line.strip()]
                    
                    self.last_refresh = file_mtime
                    logging.info(f"Loaded {len(self.proxies)} proxies from cache file")
                    
                    # If we have enough proxies, we're done
                    if len(self.proxies) >= self.min_proxies:
                        return
            
            # If we get here, we need to fetch new proxies
            self._refresh_proxies()
            
        except Exception as e:
            logging.error(f"Error loading proxies: {str(e)}")
            # Try to refresh if loading failed
            self._refresh_proxies()
    
    def _refresh_proxies(self):
        """Fetch fresh proxies from API and update cache"""
        try:
            # Use our robust SSL utility to fetch proxies
            new_proxies = self.fetch_proxies_from_api()
            
            if new_proxies and len(new_proxies) >= self.min_proxies:
                self.proxies = new_proxies
                self.last_refresh = datetime.now()
                
                # Update cache file
                with open(self.cache_file, 'w') as f:
                    f.write('\n'.join(self.proxies))
                
                logging.info(f"Updated proxy list with {len(self.proxies)} proxies")
                return True
            else:
                logging.warning(f"Failed to get enough proxies (got {len(new_proxies)}, need {self.min_proxies})")
                return False
                
        except Exception as e:
            logging.error(f"Failed to update proxy list: {str(e)}")
            return False
    
    def fetch_proxies_from_api(self):
        """
        Fetch proxies from ProxyScrape API with a fallback to disable SSL verification if needed.
        """
        url = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                proxies = response.text.splitlines()
                return [proxy for proxy in proxies if proxy.strip()]
            else:
                logging.error(f"Failed to fetch proxies: Status code {response.status_code}")
                return []
        except requests.exceptions.SSLError as e:
            logging.warning(f"SSL error occurred: {e}. Retrying with SSL verification disabled.")
            try:
                response = requests.get(url, timeout=10, verify=False)
                if response.status_code == 200:
                    proxies = response.text.splitlines()
                    return [proxy for proxy in proxies if proxy.strip()]
                else:
                    logging.error(f"Failed to fetch proxies (no SSL): Status code {response.status_code}")
                    return []
            except Exception as e2:
                logging.error(f"Failed to fetch proxies (no SSL): {e2}")
                return []
        except Exception as e:
            logging.error(f"Failed to update proxy list: {e}")
            return []
    
    def get_proxy(self):
        """
        Get a random proxy from the pool
        
        Returns:
            str: Proxy in format "ip:port" or None if no proxies available
        """
        # Check if we need to refresh proxies
        if (not self.last_refresh or 
            datetime.now() - self.last_refresh > self.refresh_interval or
            len(self.proxies) < self.min_proxies):
            self._refresh_proxies()
        
        # Return a random proxy if we have any
        if self.proxies:
            return random.choice(self.proxies)
        return None
    
    def get_proxy_dict(self, proxy_type='http'):
        """
        Get a proxy dictionary for use with requests
        
        Args:
            proxy_type (str): Protocol for proxy (http, https, socks)
            
        Returns:
            dict: Proxy dictionary or {} if no proxies available
        """
        proxy = self.get_proxy()
        if not proxy:
            return {}
        
        return {
            'http': f"{proxy_type}://{proxy}",
            'https': f"{proxy_type}://{proxy}"
        }

# Global instance for easy import
proxy_manager = ProxyManager()

# Example usage
def get_rotating_proxy():
    """Get a proxy dictionary for use with requests"""
    return proxy_manager.get_proxy_dict()