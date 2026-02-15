"""
Proxy manager for handling proxy servers, rotation, and health checks.
"""
import asyncio
import aiohttp
import requests
import time
import random
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from urllib.parse import urljoin
import socket

from app.config import settings
from app.database import get_db_session
from app.models.proxy import ProxyServer

logger = logging.getLogger(__name__)


class ProxyManager:
    """Manages proxy servers, health checks, and rotation."""

    def __init__(self):
        self.active_proxies = {}  # proxy_id -> proxy_data
        self.last_used = {}  # proxy_id -> timestamp
        self.failure_counts = {}  # proxy_id -> failure_count
        self.ban_until = {}  # proxy_id -> unban_timestamp
        self.test_urls = [
            "https://yandex.ru",
            "https://maps.yandex.ru",
            "https://yandex.ru/maps",
        ]

    def load_proxies_from_db(self) -> int:
        """Load all active proxies from database."""
        try:
            with get_db_session() as db:
                proxies = db.query(ProxyServer).filter(
                    ProxyServer.is_active == True
                ).all()

                self.active_proxies.clear()
                for proxy in proxies:
                    self.active_proxies[proxy.id] = {
                        'id': proxy.id,
                        'host': proxy.host,
                        'port': proxy.port,
                        'username': proxy.username,
                        'password': proxy.password,
                        'proxy_type': proxy.proxy_type,
                        'country': proxy.country,
                        'city': proxy.city,
                        'is_working': proxy.is_working,
                        'response_time_ms': proxy.response_time_ms,
                        'success_rate': proxy.success_rate,
                        'last_check_at': proxy.last_check_at
                    }

                logger.info(f"Loaded {len(self.active_proxies)} proxies from database")
                return len(self.active_proxies)

        except Exception as e:
            logger.error(f"Error loading proxies from database: {e}")
            return 0

    def get_proxy_url(self, proxy_data: Dict) -> str:
        """Format proxy URL for use with requests or selenium."""
        proxy_type = proxy_data.get('proxy_type', 'http')
        host = proxy_data['host']
        port = proxy_data['port']
        username = proxy_data.get('username')
        password = proxy_data.get('password')

        if username and password:
            return f"{proxy_type}://{username}:{password}@{host}:{port}"
        else:
            return f"{proxy_type}://{host}:{port}"

    def get_proxy_dict(self, proxy_data: Dict) -> Dict[str, str]:
        """Get proxy dictionary for requests library."""
        proxy_url = self.get_proxy_url(proxy_data)
        return {
            'http': proxy_url,
            'https': proxy_url
        }

    def get_available_proxy(self, exclude_ids: List[int] = None) -> Optional[Dict]:
        """Get next available proxy using round-robin with health checks."""
        try:
            exclude_ids = exclude_ids or []
            available_proxies = []

            current_time = time.time()

            for proxy_id, proxy_data in self.active_proxies.items():
                # Skip excluded proxies
                if proxy_id in exclude_ids:
                    continue

                # All active proxies are always available
                available_proxies.append((proxy_id, proxy_data))

            if not available_proxies:
                logger.warning("No available proxies found — auto-resetting all proxies")
                self._auto_reset_all_proxies()
                # Retry once after reset
                for proxy_id, proxy_data in self.active_proxies.items():
                    if proxy_id not in (exclude_ids or []):
                        available_proxies.append((proxy_id, proxy_data))
                if not available_proxies:
                    return None

            # Sort by last used time (least recently used first)
            available_proxies.sort(key=lambda x: self.last_used.get(x[0], 0))

            # Get the least recently used proxy
            proxy_id, proxy_data = available_proxies[0]
            self.last_used[proxy_id] = current_time

            logger.debug(f"Selected proxy {proxy_id}: {proxy_data['host']}:{proxy_data['port']}")
            return proxy_data

        except Exception as e:
            logger.error(f"Error getting available proxy: {e}")
            return None

    def _auto_reset_all_proxies(self):
        """Reset all proxies when none are available — prevents complete proxy starvation."""
        try:
            logger.info("Auto-resetting all proxy states (failure counts, bans)")
            self.failure_counts.clear()
            self.ban_until.clear()

            with get_db_session() as db:
                proxies = db.query(ProxyServer).filter(
                    ProxyServer.is_active == True
                ).all()
                for proxy in proxies:
                    proxy.consecutive_failures = 0
                    proxy.is_working = True
                    proxy.status = "unchecked"
                    proxy.ban_until = None

                    # Update local cache
                    if proxy.id in self.active_proxies:
                        self.active_proxies[proxy.id]['is_working'] = True
                db.commit()

            logger.info(f"Auto-reset {len(self.active_proxies)} proxies")
        except Exception as e:
            logger.error(f"Error auto-resetting proxies: {e}")

    def test_proxy(self, proxy_data: Dict, timeout: int = 15) -> Tuple[bool, float, str]:
        """Test proxy connectivity and speed using Yandex domains."""
        proxy_url = self.get_proxy_url(proxy_data)
        proxies = self.get_proxy_dict(proxy_data)

        test_url = random.choice(self.test_urls)
        start_time = time.time()
        error_message = ""

        try:
            response = requests.get(
                test_url,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=False,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            )

            response_time = (time.time() - start_time) * 1000  # Convert to milliseconds

            # Accept 2xx and 3xx as success (Yandex often redirects)
            if response.status_code < 400:
                logger.info(f"Proxy {proxy_data['host']}:{proxy_data['port']} working, HTTP {response.status_code}, Response time: {response_time:.2f}ms")
                return True, response_time, ""
            elif response.status_code in (403, 407):
                error_message = f"Proxy auth/ACL error HTTP {response.status_code}"
            else:
                error_message = f"HTTP {response.status_code}: {response.text[:100]}"

        except requests.exceptions.ProxyError as e:
            error_message = f"Proxy error: {str(e)[:200]}"
        except requests.exceptions.ConnectTimeout as e:
            error_message = f"Connection timeout: {str(e)[:200]}"
        except requests.exceptions.ReadTimeout as e:
            error_message = f"Read timeout: {str(e)[:200]}"
        except requests.exceptions.ConnectionError as e:
            error_message = f"Connection error: {str(e)[:200]}"
        except Exception as e:
            error_message = f"Unexpected error: {str(e)[:200]}"

        response_time = (time.time() - start_time) * 1000
        logger.warning(f"Proxy {proxy_data['host']}:{proxy_data['port']} failed: {error_message}")
        return False, response_time, error_message

    async def test_proxy_async(self, proxy_data: Dict, timeout: int = 15) -> Tuple[bool, float, str]:
        """Async version of proxy testing using Yandex domains."""
        proxy_url = self.get_proxy_url(proxy_data)
        test_url = random.choice(self.test_urls)
        start_time = time.time()
        error_message = ""

        try:
            connector = aiohttp.TCPConnector()
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as session:
                async with session.get(
                    test_url,
                    proxy=proxy_url,
                    allow_redirects=False,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                ) as response:
                    response_time = (time.time() - start_time) * 1000

                    # Accept 2xx and 3xx as success
                    if response.status < 400:
                        logger.info(f"Proxy {proxy_data['host']}:{proxy_data['port']} working, HTTP {response.status}")
                        return True, response_time, ""
                    elif response.status in (403, 407):
                        error_message = f"Proxy auth/ACL error HTTP {response.status}"
                    else:
                        error_message = f"HTTP {response.status}"

        except asyncio.TimeoutError:
            error_message = "Async timeout"
        except aiohttp.ClientProxyConnectionError:
            error_message = "Proxy connection error"
        except aiohttp.ClientConnectorError:
            error_message = "Connection error"
        except Exception as e:
            error_message = f"Async error: {str(e)[:200]}"

        response_time = (time.time() - start_time) * 1000
        return False, response_time, error_message

    def update_proxy_stats(self, proxy_id: int, success: bool, response_time: float = None, error_message: str = None):
        """Update proxy statistics after use."""
        try:
            with get_db_session() as db:
                proxy = db.query(ProxyServer).filter(ProxyServer.id == proxy_id).first()
                if proxy:
                    if success:
                        proxy.update_success(response_time)
                        # Reset failure count on success
                        if proxy_id in self.failure_counts:
                            del self.failure_counts[proxy_id]
                        # Remove from ban list
                        if proxy_id in self.ban_until:
                            del self.ban_until[proxy_id]
                    else:
                        proxy.update_failure(error_message)

                    db.commit()

                    # Update local cache
                    if proxy_id in self.active_proxies:
                        self.active_proxies[proxy_id].update({
                            'is_working': proxy.is_working,
                            'response_time_ms': proxy.response_time_ms,
                            'success_rate': proxy.success_rate,
                            'last_check_at': proxy.last_check_at
                        })

        except Exception as e:
            logger.error(f"Error updating proxy stats for {proxy_id}: {e}")

    def health_check_all_proxies(self) -> Dict[str, int]:
        """Perform health check on all proxies."""
        logger.info("Starting health check for all proxies")
        results = {"working": 0, "failed": 0, "total": 0}

        for proxy_id, proxy_data in self.active_proxies.items():
            success, response_time, error_message = self.test_proxy(proxy_data, timeout=15)
            self.update_proxy_stats(proxy_id, success, response_time, error_message)

            results["total"] += 1
            if success:
                results["working"] += 1
            else:
                results["failed"] += 1

            # Small delay between tests to avoid overwhelming servers
            time.sleep(random.uniform(0.5, 2))

        logger.info(f"Health check completed: {results['working']}/{results['total']} proxies working")
        return results

    async def health_check_all_proxies_async(self) -> Dict[str, int]:
        """Async version of health check for better performance."""
        logger.info("Starting async health check for all proxies")
        results = {"working": 0, "failed": 0, "total": 0}

        # Create tasks for all proxies
        tasks = []
        for proxy_id, proxy_data in self.active_proxies.items():
            task = asyncio.create_task(self.test_proxy_async(proxy_data))
            tasks.append((proxy_id, task))

        # Wait for all tasks to complete
        for proxy_id, task in tasks:
            try:
                success, response_time, error_message = await task
                self.update_proxy_stats(proxy_id, success, response_time, error_message)

                results["total"] += 1
                if success:
                    results["working"] += 1
                else:
                    results["failed"] += 1

            except Exception as e:
                logger.error(f"Error in async health check for proxy {proxy_id}: {e}")
                results["total"] += 1
                results["failed"] += 1

        logger.info(f"Async health check completed: {results['working']}/{results['total']} proxies working")
        return results

    def get_proxy_by_location(self, country: str = None, city: str = None) -> Optional[Dict]:
        """Get proxy by geographic location."""
        matching_proxies = []

        for proxy_id, proxy_data in self.active_proxies.items():
            if not proxy_data.get('is_working', True):
                continue

            match = True
            if country and proxy_data.get('country', '').lower() != country.lower():
                match = False
            if city and proxy_data.get('city', '').lower() != city.lower():
                match = False

            if match:
                matching_proxies.append((proxy_id, proxy_data))

        if matching_proxies:
            # Return random proxy from matching ones
            proxy_id, proxy_data = random.choice(matching_proxies)
            self.last_used[proxy_id] = time.time()
            return proxy_data

        return None

    def get_fastest_proxy(self, limit: int = 5) -> Optional[Dict]:
        """Get the fastest responding proxy."""
        working_proxies = []

        for proxy_id, proxy_data in self.active_proxies.items():
            if not proxy_data.get('is_working', True):
                continue

            response_time = proxy_data.get('response_time_ms', float('inf'))
            if response_time < float('inf'):
                working_proxies.append((response_time, proxy_id, proxy_data))

        if working_proxies:
            # Sort by response time and get top performers
            working_proxies.sort(key=lambda x: x[0])
            top_proxies = working_proxies[:limit]

            # Return random proxy from top performers
            _, proxy_id, proxy_data = random.choice(top_proxies)
            self.last_used[proxy_id] = time.time()
            return proxy_data

        return None

    def add_proxy(self, host: str, port: int, username: str = None, password: str = None,
                  proxy_type: str = "http", country: str = None, city: str = None) -> int:
        """Add new proxy to database and local cache."""
        try:
            with get_db_session() as db:
                proxy = ProxyServer(
                    name=f"{host}:{port}",
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    proxy_type=proxy_type,
                    country=country,
                    city=city
                )
                db.add(proxy)
                db.commit()
                db.refresh(proxy)

                # Add to local cache
                self.active_proxies[proxy.id] = {
                    'id': proxy.id,
                    'host': host,
                    'port': port,
                    'username': username,
                    'password': password,
                    'proxy_type': proxy_type,
                    'country': country,
                    'city': city,
                    'is_working': True,
                    'response_time_ms': None,
                    'success_rate': 0.0
                }

                logger.info(f"Added new proxy: {host}:{port}")
                return proxy.id

        except Exception as e:
            logger.error(f"Error adding proxy {host}:{port}: {e}")
            raise

    def remove_proxy(self, proxy_id: int):
        """Remove proxy from database and local cache."""
        try:
            with get_db_session() as db:
                proxy = db.query(ProxyServer).filter(ProxyServer.id == proxy_id).first()
                if proxy:
                    db.delete(proxy)
                    db.commit()

            # Remove from local cache
            if proxy_id in self.active_proxies:
                del self.active_proxies[proxy_id]
            if proxy_id in self.last_used:
                del self.last_used[proxy_id]
            if proxy_id in self.failure_counts:
                del self.failure_counts[proxy_id]
            if proxy_id in self.ban_until:
                del self.ban_until[proxy_id]

            logger.info(f"Removed proxy {proxy_id}")

        except Exception as e:
            logger.error(f"Error removing proxy {proxy_id}: {e}")
            raise

    def get_proxy_stats(self) -> Dict:
        """Get overall proxy statistics."""
        total_proxies = len(self.active_proxies)
        working_proxies = sum(1 for p in self.active_proxies.values() if p.get('is_working', True))
        banned_proxies = len(self.ban_until)

        response_times = [
            p.get('response_time_ms', 0) for p in self.active_proxies.values()
            if p.get('response_time_ms') is not None and p.get('is_working', True)
        ]

        avg_response_time = sum(response_times) / len(response_times) if response_times else 0

        return {
            "total_proxies": total_proxies,
            "working_proxies": working_proxies,
            "failed_proxies": total_proxies - working_proxies,
            "banned_proxies": banned_proxies,
            "average_response_time": round(avg_response_time, 2),
            "fastest_response_time": min(response_times) if response_times else 0,
            "slowest_response_time": max(response_times) if response_times else 0
        }

    def cleanup_expired_bans(self):
        """Remove expired bans from proxy ban list."""
        current_time = time.time()
        expired_bans = [
            proxy_id for proxy_id, ban_time in self.ban_until.items()
            if current_time > ban_time
        ]

        for proxy_id in expired_bans:
            del self.ban_until[proxy_id]
            if proxy_id in self.failure_counts:
                self.failure_counts[proxy_id] = 0

        if expired_bans:
            logger.info(f"Cleaned up {len(expired_bans)} expired proxy bans")

    def rotate_proxy_for_profile(self, current_proxy_id: int = None) -> Optional[Dict]:
        """Get a different proxy for rotation."""
        exclude_list = [current_proxy_id] if current_proxy_id else []
        return self.get_available_proxy(exclude_ids=exclude_list)