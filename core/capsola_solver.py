"""
Capsola Cloud Captcha Solver для Яндекс SmartCaptcha и PazlCaptcha
"""
import requests
import time
import base64
import json
import logging
from typing import Optional, Dict, Tuple, List
from io import BytesIO
from PIL import Image

logger = logging.getLogger(__name__)


class CapsolaSolver:
    """Решатель капчи через Capsola Cloud API"""
    
    def __init__(self, api_key: str):
        """
        Инициализация решателя Capsola
        
        Args:
            api_key: API ключ от capsola.cloud
        """
        self.api_key = api_key
        self.url_create = 'https://api.capsola.cloud/create'
        self.url_result = 'https://api.capsola.cloud/result'
        self.headers = {
            'Content-type': 'application/json',
            'X-API-Key': api_key
        }
        
    def _poll_result(self, task_id: str, max_wait: int = 60) -> Optional[Dict]:
        """
        Поллинг результата задачи Capsola.
        
        Args:
            task_id: ID задачи
            max_wait: Максимальное время ожидания (секунды)
            
        Returns:
            Dict с результатом или None
        """
        start_time = time.time()
        while time.time() - start_time < max_wait:
            time.sleep(2)
            
            result_data = {'id': task_id}
            result_response = requests.post(
                url=self.url_result,
                json=result_data,
                headers=self.headers
            )
            result = result_response.json()
            
            logger.info(f"Capsola result: {result}")
            
            if result.get('status') == 1:
                logger.info(f"✅ Капча решена! Результат: {result.get('response')}")
                return result
                
            if result.get('status') == 0 and result.get('response') != 'CAPCHA_NOT_READY':
                logger.error(f"Ошибка решения капчи: {result}")
                return None
                
            logger.debug("Капча ещё не решена, ждём...")
            
        logger.error(f"Превышен таймаут ожидания результата ({max_wait}s)")
        return None

    def solve_smart_captcha(self, click_image: bytes, task_image: bytes, max_wait: int = 60) -> Optional[Dict]:
        """
        Решить Яндекс SmartCaptcha через Capsola
        
        Args:
            click_image: Изображение с заданием (bytes)
            task_image: Изображение сетки для выбора (bytes)
            max_wait: Максимальное время ожидания результата (секунды)
            
        Returns:
            Dict с результатом решения или None при ошибке
        """
        try:
            # Конвертируем изображения в base64
            click_base64 = base64.b64encode(click_image).decode('utf-8')
            task_base64 = base64.b64encode(task_image).decode('utf-8')
            
            logger.info("Отправляем SmartCaptcha в Capsola...")
            
            # Создаём задачу
            data = {
                'type': 'SmartCaptcha',
                'click': click_base64,
                'task': task_base64,
            }
            
            response = requests.post(url=self.url_create, json=data, headers=self.headers)
            response_data = response.json()
            
            logger.info(f"Capsola create response: {response_data}")
            
            if response_data.get('status') != 1:
                logger.error(f"Ошибка создания задачи: {response_data}")
                return None
                
            task_id = response_data.get('response')
            if not task_id:
                logger.error("Не получен task_id от Capsola")
                return None
                
            logger.info(f"Задача SmartCaptcha создана, ID: {task_id}")
            return self._poll_result(task_id, max_wait)
            
        except Exception as e:
            logger.error(f"Ошибка при решении SmartCaptcha через Capsola: {e}")
            return None

    def solve_pazl_captcha_v1(self, page_html: str, max_wait: int = 90) -> Optional[Dict]:
        """
        Решить PazlCaptcha V1 через Capsola (отправка полной HTML-страницы).
        
        Используется для Silhouette/Puzzle капчи Яндекса.
        Результат содержит step number — координаты/порядок кликов.
        
        Args:
            page_html: Полный HTML страницы с капчей
            max_wait: Максимальное время ожидания результата (секунды)
            
        Returns:
            Dict с результатом решения или None при ошибке
        """
        try:
            # Кодируем HTML в base64
            html_base64 = base64.b64encode(page_html.encode('utf-8')).decode('utf-8')
            
            logger.info(f"Отправляем PazlCaptcha V1 в Capsola (HTML size: {len(page_html)} chars)...")
            
            data = {
                'type': 'PazlCaptcha',
                'task': html_base64,
            }
            
            response = requests.post(url=self.url_create, json=data, headers=self.headers, timeout=30)
            response_data = response.json()
            
            logger.info(f"Capsola PazlCaptcha V1 create response: {response_data}")
            
            if response_data.get('status') != 1:
                logger.error(f"Ошибка создания задачи PazlCaptcha V1: {response_data}")
                return None
                
            task_id = response_data.get('response')
            if not task_id:
                logger.error("Не получен task_id от Capsola для PazlCaptcha V1")
                return None
                
            logger.info(f"Задача PazlCaptcha V1 создана, ID: {task_id}")
            return self._poll_result(task_id, max_wait)
            
        except Exception as e:
            logger.error(f"Ошибка при решении PazlCaptcha V1 через Capsola: {e}")
            return None

    def solve_pazl_captcha_v2(self, image_data: bytes, permutations: List, max_wait: int = 90) -> Optional[Dict]:
        """
        Решить PazlCaptcha V2 через Capsola (отправка изображения + список перестановок).
        
        Args:
            image_data: Изображение капчи (bytes)
            permutations: Список перестановок (list)
            max_wait: Максимальное время ожидания результата (секунды)
            
        Returns:
            Dict с результатом решения или None при ошибке
        """
        try:
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            task_json = json.dumps(permutations)
            
            logger.info(f"Отправляем PazlCaptcha V2 в Capsola (image: {len(image_data)} bytes, permutations: {len(permutations)} items)...")
            
            data = {
                'type': 'PazlCaptchaV2',
                'image': image_base64,
                'task': task_json,
            }
            
            response = requests.post(url=self.url_create, json=data, headers=self.headers, timeout=30)
            response_data = response.json()
            
            logger.info(f"Capsola PazlCaptcha V2 create response: {response_data}")
            
            if response_data.get('status') != 1:
                logger.error(f"Ошибка создания задачи PazlCaptcha V2: {response_data}")
                return None
                
            task_id = response_data.get('response')
            if not task_id:
                logger.error("Не получен task_id от Capsola для PazlCaptcha V2")
                return None
                
            logger.info(f"Задача PazlCaptcha V2 создана, ID: {task_id}")
            return self._poll_result(task_id, max_wait)
            
        except Exception as e:
            logger.error(f"Ошибка при решении PazlCaptcha V2 через Capsola: {e}")
            return None

    def solve_from_screenshot(self, screenshot_path: str) -> Optional[Dict]:
        """
        Решить капчу из скриншота
        
        Args:
            screenshot_path: Путь к скриншоту с капчей
            
        Returns:
            Dict с результатом или None
        """
        try:
            # Загружаем скриншот
            with open(screenshot_path, 'rb') as f:
                screenshot_data = f.read()
            
            # Для SmartCaptcha нужно разделить на две части:
            # 1. Задание (верхняя часть)
            # 2. Сетка для выбора (нижняя часть)
            
            # Открываем изображение
            img = Image.open(BytesIO(screenshot_data))
            width, height = img.size
            
            # Примерное разделение (может потребоваться настройка)
            # Задание обычно в верхней 30% части
            # Сетка в остальной части
            task_height = int(height * 0.3)
            
            # Вырезаем задание (верх)
            click_img = img.crop((0, 0, width, task_height))
            click_buffer = BytesIO()
            click_img.save(click_buffer, format='PNG')
            click_data = click_buffer.getvalue()
            
            # Вырезаем сетку (низ)
            task_img = img.crop((0, task_height, width, height))
            task_buffer = BytesIO()
            task_img.save(task_buffer, format='PNG')
            task_data = task_buffer.getvalue()
            
            # Решаем
            return self.solve_smart_captcha(click_data, task_data)
            
        except Exception as e:
            logger.error(f"Ошибка при обработке скриншота: {e}")
            return None


# Для удобства использования
def create_capsola_solver(api_key: str = None) -> CapsolaSolver:
    """
    Создать экземпляр решателя Capsola
    
    Args:
        api_key: API ключ (если None, берётся из конфига)
    """
    if not api_key:
        try:
            from app.config import settings
            api_key = settings.capsola_api_key
        except Exception:
            import os
            api_key = os.getenv('YANDEX_BOT_CAPSOLA_API_KEY', '9f8a1a9b-4322-4b8a-91ec-49192cdbaeb9')
    
    return CapsolaSolver(api_key)
