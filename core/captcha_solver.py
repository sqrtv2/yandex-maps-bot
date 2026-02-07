"""
Captcha solver integration for various anti-captcha services.
"""
import time
import base64
import requests
import logging
from typing import Dict, Optional, Tuple, Union
from io import BytesIO
from PIL import Image
import asyncio
import aiohttp

from app.config import settings
from app.database import get_setting

logger = logging.getLogger(__name__)


class CaptchaSolver:
    """Integrates with various captcha solving services."""

    def __init__(self):
        self.api_key = None
        self.service = None
        self.timeout = 120  # Default timeout in seconds
        self._load_config()

    def _load_config(self):
        """Load captcha solver configuration."""
        try:
            self.api_key = get_setting('anticaptcha_api_key', settings.anticaptcha_api_key)
            self.service = get_setting('anticaptcha_service', settings.anticaptcha_service)
            self.timeout = get_setting('captcha_timeout_seconds', settings.captcha_timeout)

            if not self.api_key:
                logger.warning("No anti-captcha API key configured")
            else:
                logger.info(f"Captcha solver configured: {self.service}")

        except Exception as e:
            logger.error(f"Error loading captcha solver config: {e}")

    def is_configured(self) -> bool:
        """Check if captcha solver is properly configured."""
        return bool(self.api_key and self.service)

    def solve_image_captcha(self, image_data: Union[str, bytes], **kwargs) -> Optional[str]:
        """Solve image-based captcha."""
        if not self.is_configured():
            logger.error("Captcha solver not configured")
            return None

        try:
            # Convert image data to base64 if needed
            if isinstance(image_data, bytes):
                image_b64 = base64.b64encode(image_data).decode('utf-8')
            else:
                image_b64 = image_data

            if self.service == '2captcha':
                return self._solve_2captcha_image(image_b64, **kwargs)
            elif self.service == 'anticaptcha':
                return self._solve_anticaptcha_image(image_b64, **kwargs)
            else:
                logger.error(f"Unsupported captcha service: {self.service}")
                return None

        except Exception as e:
            logger.error(f"Error solving image captcha: {e}")
            return None

    def solve_recaptcha_v2(self, site_key: str, page_url: str, **kwargs) -> Optional[str]:
        """Solve reCAPTCHA v2."""
        if not self.is_configured():
            logger.error("Captcha solver not configured")
            return None

        try:
            if self.service == '2captcha':
                return self._solve_2captcha_recaptcha_v2(site_key, page_url, **kwargs)
            elif self.service == 'anticaptcha':
                return self._solve_anticaptcha_recaptcha_v2(site_key, page_url, **kwargs)
            else:
                logger.error(f"Unsupported captcha service: {self.service}")
                return None

        except Exception as e:
            logger.error(f"Error solving reCAPTCHA v2: {e}")
            return None

    def solve_recaptcha_v3(self, site_key: str, page_url: str, action: str = "verify",
                          min_score: float = 0.3, **kwargs) -> Optional[str]:
        """Solve reCAPTCHA v3."""
        if not self.is_configured():
            logger.error("Captcha solver not configured")
            return None

        try:
            if self.service == '2captcha':
                return self._solve_2captcha_recaptcha_v3(site_key, page_url, action, min_score, **kwargs)
            elif self.service == 'anticaptcha':
                return self._solve_anticaptcha_recaptcha_v3(site_key, page_url, action, min_score, **kwargs)
            else:
                logger.error(f"Unsupported captcha service: {self.service}")
                return None

        except Exception as e:
            logger.error(f"Error solving reCAPTCHA v3: {e}")
            return None

    def solve_hcaptcha(self, site_key: str, page_url: str, **kwargs) -> Optional[str]:
        """Solve hCaptcha."""
        if not self.is_configured():
            logger.error("Captcha solver not configured")
            return None

        try:
            if self.service == '2captcha':
                return self._solve_2captcha_hcaptcha(site_key, page_url, **kwargs)
            elif self.service == 'anticaptcha':
                return self._solve_anticaptcha_hcaptcha(site_key, page_url, **kwargs)
            else:
                logger.error(f"Unsupported captcha service: {self.service}")
                return None

        except Exception as e:
            logger.error(f"Error solving hCaptcha: {e}")
            return None

    # 2captcha implementations

    def _solve_2captcha_image(self, image_b64: str, **kwargs) -> Optional[str]:
        """Solve image captcha using 2captcha."""
        try:
            # Submit captcha
            submit_url = "http://2captcha.com/in.php"
            submit_data = {
                'method': 'base64',
                'key': self.api_key,
                'body': image_b64,
                'json': 1
            }

            # Add optional parameters
            if 'numeric' in kwargs:
                submit_data['numeric'] = kwargs['numeric']
            if 'min_len' in kwargs:
                submit_data['min_len'] = kwargs['min_len']
            if 'max_len' in kwargs:
                submit_data['max_len'] = kwargs['max_len']

            response = requests.post(submit_url, data=submit_data, timeout=30)
            result = response.json()

            if result['status'] != 1:
                logger.error(f"2captcha submit error: {result.get('error_text', 'Unknown error')}")
                return None

            captcha_id = result['request']
            logger.info(f"2captcha captcha submitted: {captcha_id}")

            # Poll for result
            return self._poll_2captcha_result(captcha_id)

        except Exception as e:
            logger.error(f"Error solving 2captcha image: {e}")
            return None

    def _solve_2captcha_recaptcha_v2(self, site_key: str, page_url: str, **kwargs) -> Optional[str]:
        """Solve reCAPTCHA v2 using 2captcha."""
        try:
            # Submit captcha
            submit_url = "http://2captcha.com/in.php"
            submit_data = {
                'method': 'userrecaptcha',
                'key': self.api_key,
                'googlekey': site_key,
                'pageurl': page_url,
                'json': 1
            }

            # Add proxy if provided
            if 'proxy' in kwargs:
                proxy = kwargs['proxy']
                submit_data.update({
                    'proxy': proxy['proxy'],
                    'proxytype': proxy.get('type', 'HTTP').upper()
                })

            response = requests.post(submit_url, data=submit_data, timeout=30)
            result = response.json()

            if result['status'] != 1:
                logger.error(f"2captcha reCAPTCHA submit error: {result.get('error_text', 'Unknown error')}")
                return None

            captcha_id = result['request']
            logger.info(f"2captcha reCAPTCHA submitted: {captcha_id}")

            # Poll for result (reCAPTCHA takes longer)
            return self._poll_2captcha_result(captcha_id, timeout=180)

        except Exception as e:
            logger.error(f"Error solving 2captcha reCAPTCHA v2: {e}")
            return None

    def _solve_2captcha_recaptcha_v3(self, site_key: str, page_url: str, action: str,
                                    min_score: float, **kwargs) -> Optional[str]:
        """Solve reCAPTCHA v3 using 2captcha."""
        try:
            # Submit captcha
            submit_url = "http://2captcha.com/in.php"
            submit_data = {
                'method': 'userrecaptcha',
                'key': self.api_key,
                'googlekey': site_key,
                'pageurl': page_url,
                'version': 'v3',
                'action': action,
                'min_score': min_score,
                'json': 1
            }

            response = requests.post(submit_url, data=submit_data, timeout=30)
            result = response.json()

            if result['status'] != 1:
                logger.error(f"2captcha reCAPTCHA v3 submit error: {result.get('error_text', 'Unknown error')}")
                return None

            captcha_id = result['request']
            logger.info(f"2captcha reCAPTCHA v3 submitted: {captcha_id}")

            return self._poll_2captcha_result(captcha_id, timeout=180)

        except Exception as e:
            logger.error(f"Error solving 2captcha reCAPTCHA v3: {e}")
            return None

    def _solve_2captcha_hcaptcha(self, site_key: str, page_url: str, **kwargs) -> Optional[str]:
        """Solve hCaptcha using 2captcha."""
        try:
            # Submit captcha
            submit_url = "http://2captcha.com/in.php"
            submit_data = {
                'method': 'hcaptcha',
                'key': self.api_key,
                'sitekey': site_key,
                'pageurl': page_url,
                'json': 1
            }

            response = requests.post(submit_url, data=submit_data, timeout=30)
            result = response.json()

            if result['status'] != 1:
                logger.error(f"2captcha hCaptcha submit error: {result.get('error_text', 'Unknown error')}")
                return None

            captcha_id = result['request']
            logger.info(f"2captcha hCaptcha submitted: {captcha_id}")

            return self._poll_2captcha_result(captcha_id, timeout=180)

        except Exception as e:
            logger.error(f"Error solving 2captcha hCaptcha: {e}")
            return None

    def _poll_2captcha_result(self, captcha_id: str, timeout: int = None) -> Optional[str]:
        """Poll 2captcha for result."""
        timeout = timeout or self.timeout
        result_url = "http://2captcha.com/res.php"

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(result_url, params={
                    'key': self.api_key,
                    'action': 'get',
                    'id': captcha_id,
                    'json': 1
                }, timeout=30)

                result = response.json()

                if result['status'] == 1:
                    logger.info(f"2captcha solved: {captcha_id}")
                    return result['request']
                elif result.get('error_text') == 'CAPCHA_NOT_READY':
                    # Still processing, wait and retry
                    time.sleep(5)
                    continue
                else:
                    logger.error(f"2captcha error: {result.get('error_text', 'Unknown error')}")
                    return None

            except Exception as e:
                logger.warning(f"Error polling 2captcha result: {e}")
                time.sleep(5)
                continue

        logger.error(f"2captcha timeout after {timeout} seconds")
        return None

    # Anti-Captcha implementations

    def _solve_anticaptcha_image(self, image_b64: str, **kwargs) -> Optional[str]:
        """Solve image captcha using anti-captcha."""
        try:
            # Create task
            create_url = "https://api.anti-captcha.com/createTask"
            task_data = {
                "clientKey": self.api_key,
                "task": {
                    "type": "ImageToTextTask",
                    "body": image_b64
                }
            }

            # Add optional parameters
            if 'numeric' in kwargs:
                task_data["task"]["numeric"] = kwargs['numeric']
            if 'min_len' in kwargs:
                task_data["task"]["minLength"] = kwargs['min_len']
            if 'max_len' in kwargs:
                task_data["task"]["maxLength"] = kwargs['max_len']

            response = requests.post(create_url, json=task_data, timeout=30)
            result = response.json()

            if result.get('errorId') != 0:
                logger.error(f"Anti-captcha create error: {result.get('errorDescription')}")
                return None

            task_id = result['taskId']
            logger.info(f"Anti-captcha task created: {task_id}")

            return self._poll_anticaptcha_result(task_id)

        except Exception as e:
            logger.error(f"Error solving anti-captcha image: {e}")
            return None

    def _solve_anticaptcha_recaptcha_v2(self, site_key: str, page_url: str, **kwargs) -> Optional[str]:
        """Solve reCAPTCHA v2 using anti-captcha."""
        try:
            # Create task
            create_url = "https://api.anti-captcha.com/createTask"
            task_data = {
                "clientKey": self.api_key,
                "task": {
                    "type": "NoCaptchaTaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": site_key
                }
            }

            response = requests.post(create_url, json=task_data, timeout=30)
            result = response.json()

            if result.get('errorId') != 0:
                logger.error(f"Anti-captcha reCAPTCHA create error: {result.get('errorDescription')}")
                return None

            task_id = result['taskId']
            logger.info(f"Anti-captcha reCAPTCHA task created: {task_id}")

            return self._poll_anticaptcha_result(task_id, timeout=180)

        except Exception as e:
            logger.error(f"Error solving anti-captcha reCAPTCHA v2: {e}")
            return None

    def _solve_anticaptcha_recaptcha_v3(self, site_key: str, page_url: str, action: str,
                                       min_score: float, **kwargs) -> Optional[str]:
        """Solve reCAPTCHA v3 using anti-captcha."""
        try:
            # Create task
            create_url = "https://api.anti-captcha.com/createTask"
            task_data = {
                "clientKey": self.api_key,
                "task": {
                    "type": "RecaptchaV3TaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                    "pageAction": action,
                    "minScore": min_score
                }
            }

            response = requests.post(create_url, json=task_data, timeout=30)
            result = response.json()

            if result.get('errorId') != 0:
                logger.error(f"Anti-captcha reCAPTCHA v3 create error: {result.get('errorDescription')}")
                return None

            task_id = result['taskId']
            logger.info(f"Anti-captcha reCAPTCHA v3 task created: {task_id}")

            return self._poll_anticaptcha_result(task_id, timeout=180)

        except Exception as e:
            logger.error(f"Error solving anti-captcha reCAPTCHA v3: {e}")
            return None

    def _solve_anticaptcha_hcaptcha(self, site_key: str, page_url: str, **kwargs) -> Optional[str]:
        """Solve hCaptcha using anti-captcha."""
        try:
            # Create task
            create_url = "https://api.anti-captcha.com/createTask"
            task_data = {
                "clientKey": self.api_key,
                "task": {
                    "type": "HCaptchaTaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": site_key
                }
            }

            response = requests.post(create_url, json=task_data, timeout=30)
            result = response.json()

            if result.get('errorId') != 0:
                logger.error(f"Anti-captcha hCaptcha create error: {result.get('errorDescription')}")
                return None

            task_id = result['taskId']
            logger.info(f"Anti-captcha hCaptcha task created: {task_id}")

            return self._poll_anticaptcha_result(task_id, timeout=180)

        except Exception as e:
            logger.error(f"Error solving anti-captcha hCaptcha: {e}")
            return None

    def _poll_anticaptcha_result(self, task_id: int, timeout: int = None) -> Optional[str]:
        """Poll anti-captcha for result."""
        timeout = timeout or self.timeout
        result_url = "https://api.anti-captcha.com/getTaskResult"

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                data = {
                    "clientKey": self.api_key,
                    "taskId": task_id
                }

                response = requests.post(result_url, json=data, timeout=30)
                result = response.json()

                if result.get('errorId') != 0:
                    logger.error(f"Anti-captcha error: {result.get('errorDescription')}")
                    return None

                if result.get('status') == 'ready':
                    solution = result.get('solution', {})
                    answer = solution.get('gRecaptchaResponse') or solution.get('text') or solution.get('token')
                    if answer:
                        logger.info(f"Anti-captcha solved: {task_id}")
                        return answer
                    else:
                        logger.error("Anti-captcha returned empty solution")
                        return None
                elif result.get('status') == 'processing':
                    # Still processing, wait and retry
                    time.sleep(5)
                    continue
                else:
                    logger.error(f"Anti-captcha unknown status: {result.get('status')}")
                    return None

            except Exception as e:
                logger.warning(f"Error polling anti-captcha result: {e}")
                time.sleep(5)
                continue

        logger.error(f"Anti-captcha timeout after {timeout} seconds")
        return None

    # Utility methods

    def get_balance(self) -> Optional[float]:
        """Get account balance."""
        if not self.is_configured():
            return None

        try:
            if self.service == '2captcha':
                url = "http://2captcha.com/res.php"
                params = {
                    'key': self.api_key,
                    'action': 'getbalance',
                    'json': 1
                }
                response = requests.get(url, params=params, timeout=10)
                result = response.json()

                if result['status'] == 1:
                    return float(result['request'])
                else:
                    logger.error(f"2captcha balance error: {result.get('error_text')}")
                    return None

            elif self.service == 'anticaptcha':
                url = "https://api.anti-captcha.com/getBalance"
                data = {"clientKey": self.api_key}
                response = requests.post(url, json=data, timeout=10)
                result = response.json()

                if result.get('errorId') == 0:
                    return float(result.get('balance', 0))
                else:
                    logger.error(f"Anti-captcha balance error: {result.get('errorDescription')}")
                    return None

        except Exception as e:
            logger.error(f"Error getting balance: {e}")
            return None

    def test_api_key(self) -> bool:
        """Test if API key is working."""
        balance = self.get_balance()
        return balance is not None

    def capture_element_screenshot(self, driver, element) -> Optional[bytes]:
        """Capture screenshot of specific element for captcha solving."""
        try:
            # Take full page screenshot
            screenshot = driver.get_screenshot_as_png()

            # Get element location and size
            location = element.location
            size = element.size

            # Open image and crop to element
            img = Image.open(BytesIO(screenshot))
            left = location['x']
            top = location['y']
            right = location['x'] + size['width']
            bottom = location['y'] + size['height']

            cropped_img = img.crop((left, top, right, bottom))

            # Convert to bytes
            img_bytes = BytesIO()
            cropped_img.save(img_bytes, format='PNG')
            return img_bytes.getvalue()

        except Exception as e:
            logger.error(f"Error capturing element screenshot: {e}")
            return None

    async def solve_captcha_async(self, captcha_type: str, **kwargs) -> Optional[str]:
        """Async wrapper for captcha solving."""
        loop = asyncio.get_event_loop()

        if captcha_type == 'image':
            return await loop.run_in_executor(None, self.solve_image_captcha, kwargs.get('image_data'), kwargs)
        elif captcha_type == 'recaptcha_v2':
            return await loop.run_in_executor(None, self.solve_recaptcha_v2, kwargs.get('site_key'), kwargs.get('page_url'), kwargs)
        elif captcha_type == 'recaptcha_v3':
            return await loop.run_in_executor(None, self.solve_recaptcha_v3,
                                            kwargs.get('site_key'), kwargs.get('page_url'),
                                            kwargs.get('action', 'verify'), kwargs.get('min_score', 0.3), kwargs)
        elif captcha_type == 'hcaptcha':
            return await loop.run_in_executor(None, self.solve_hcaptcha, kwargs.get('site_key'), kwargs.get('page_url'), kwargs)
        else:
            logger.error(f"Unsupported async captcha type: {captcha_type}")
            return None