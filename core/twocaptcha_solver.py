"""
2captcha Cloud Captcha Solver — fallback для SmartCaptcha (silhouette / grid).

Используется когда основной провайдер (Capsola) вернул ошибку или неверный ответ.
Реализует ТОТ ЖЕ интерфейс, что и CapsolaSolver, чтобы вызывающий код мог
просто попробовать другой солвер с теми же аргументами.

Под капотом — 2captcha `CoordinatesTask`:
  - принимает одно изображение + текстовый комментарий
  - возвращает массив координат, по которым операторы 2captcha кликнули

Для совместимости с Capsola мы:
  1. Склеиваем `click_image` и `task_image` вертикально (меньшее сверху).
  2. Передаём в 2captcha с инструкцией.
  3. Переводим координаты обратно к большему изображению (вычитаем высоту меньшего).
  4. Возвращаем ответ в Capsola-совместимом формате
     `{'status': 1, 'response': 'coordinates:x=..,y=..;x=..,y=..'}`.

Для kaleidoscope (PazlCaptcha V2 — slider step) 2captcha не подходит и
здесь не реализован.
"""
import base64
import logging
import time
from io import BytesIO
from typing import Optional, Dict

import requests
from PIL import Image

logger = logging.getLogger(__name__)


class TwoCaptchaSolver:
    """Fallback-солвер на базе 2captcha.com (CoordinatesTask)."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.url_create = 'https://api.2captcha.com/createTask'
        self.url_result = 'https://api.2captcha.com/getTaskResult'
        self.url_balance = 'https://api.2captcha.com/getBalance'

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _create_task(self, task: Dict) -> Optional[str]:
        """Создать задачу. Возвращает taskId либо None."""
        try:
            payload = {'clientKey': self.api_key, 'task': task}
            resp = requests.post(self.url_create, json=payload, timeout=30)
            data = resp.json()
            if data.get('errorId') != 0:
                logger.error(
                    f"2captcha createTask error: id={data.get('errorId')}, "
                    f"code={data.get('errorCode')}, desc={data.get('errorDescription')}"
                )
                return None
            return data.get('taskId')
        except Exception as e:
            logger.error(f"2captcha createTask exception: {e}")
            return None

    def _poll_result(self, task_id, max_wait: int = 120) -> Optional[Dict]:
        """Поллит результат до max_wait секунд. Возвращает solution-словарь либо None."""
        deadline = time.time() + max_wait
        # 2captcha рекомендует первый запрос через ~5с после createTask
        time.sleep(5)
        while time.time() < deadline:
            try:
                resp = requests.post(
                    self.url_result,
                    json={'clientKey': self.api_key, 'taskId': task_id},
                    timeout=15,
                )
                data = resp.json()
                if data.get('errorId') != 0:
                    logger.error(
                        f"2captcha getTaskResult error: id={data.get('errorId')}, "
                        f"code={data.get('errorCode')}"
                    )
                    return None
                status = data.get('status')
                if status == 'ready':
                    sol = data.get('solution') or {}
                    logger.info(f"✅ 2captcha solved (taskId={task_id})")
                    return sol
                # processing → ждём ещё
            except Exception as e:
                logger.warning(f"2captcha poll exception: {e}")
            time.sleep(3)
        logger.error(f"2captcha timeout waiting for taskId={task_id} ({max_wait}s)")
        return None

    @staticmethod
    def _combine_images(click_image: bytes, task_image: bytes):
        """Склеить два изображения вертикально (меньшее по высоте — сверху).

        Возвращает (combined_png_bytes, top_height, bigger_is_top).
        `top_height` нужен, чтобы потом сдвинуть координаты обратно к большому изображению.
        `bigger_is_top` = True если click_image больше (т.е. сверху task, снизу click).
        """
        a = Image.open(BytesIO(click_image)).convert('RGB')
        b = Image.open(BytesIO(task_image)).convert('RGB')
        a_area = a.width * a.height
        b_area = b.width * b.height
        # Меньшее по площади — сверху, большее — снизу. Координаты вернём относительно большего.
        if a_area >= b_area:
            top, bottom, bigger_is_top = b, a, False  # click больше → bottom
        else:
            top, bottom, bigger_is_top = a, b, True   # task больше → bottom
        w = max(top.width, bottom.width)
        h = top.height + bottom.height
        combined = Image.new('RGB', (w, h), (255, 255, 255))
        combined.paste(top, (0, 0))
        combined.paste(bottom, (0, top.height))
        buf = BytesIO()
        combined.save(buf, format='PNG')
        return buf.getvalue(), top.height, bigger_is_top

    # ------------------------------------------------------------------ #
    # public API (Capsola-compatible)
    # ------------------------------------------------------------------ #

    def get_balance(self) -> Optional[float]:
        try:
            resp = requests.post(self.url_balance, json={'clientKey': self.api_key}, timeout=15)
            data = resp.json()
            if data.get('errorId') != 0:
                return None
            return float(data.get('balance', 0))
        except Exception as e:
            logger.warning(f"2captcha getBalance failed: {e}")
            return None

    def solve_smart_captcha(self, click_image: bytes, task_image: bytes,
                            max_wait: int = 120,
                            instruction: Optional[str] = None) -> Optional[Dict]:
        """Решить SmartCaptcha-подобную капчу (silhouette / image grid).

        Args:
            click_image: первое изображение (как в Capsola.solve_smart_captcha).
            task_image: второе изображение.
            max_wait: общий бюджет ожидания результата, сек.
            instruction: текстовый комментарий для оператора 2captcha. Если None —
                ставится дефолтная двуязычная инструкция.

        Returns:
            Capsola-совместимый dict `{'status': 1, 'response': 'coordinates:x=..,y=..;...'}`
            либо None при ошибке.
        """
        try:
            combined_png, top_h, bigger_is_top = self._combine_images(click_image, task_image)
            body_b64 = base64.b64encode(combined_png).decode('ascii')

            comment = instruction or (
                "Yandex SmartCaptcha. Image is two parts stacked: small task panel on TOP, "
                "main image on BOTTOM. Click in correct order on objects in the BOTTOM image "
                "that match the icons/instruction shown in the TOP panel.\n"
                "Яндекс капча. Сверху подсказка, снизу основное изображение. Кликните по "
                "объектам в нижней части в правильном порядке согласно подсказке сверху."
            )

            task = {
                'type': 'CoordinatesTask',
                'body': body_b64,
                'comment': comment,
            }
            logger.info(
                f"🔄 Sending to 2captcha CoordinatesTask "
                f"(combined={len(combined_png)}b, top_h={top_h}, bigger_is_top={bigger_is_top})"
            )
            task_id = self._create_task(task)
            if not task_id:
                return None

            sol = self._poll_result(task_id, max_wait=max_wait)
            if not sol:
                return None

            coords = sol.get('coordinates') or []
            if not coords:
                logger.error(f"2captcha returned solution without coordinates: {sol}")
                return None

            # Сдвиг: если большее изображение СНИЗУ, координаты в нижней половине нужно
            # перевести в систему большего (вычесть top_h). Точки в верхней области
            # (y < top_h) — это клики по инструкции; их отбрасываем.
            parts = []
            for c in coords:
                try:
                    x = float(c.get('x', 0))
                    y = float(c.get('y', 0))
                except (TypeError, ValueError):
                    continue
                y_adj = y - top_h
                if y_adj < 0:
                    # Оператор кликнул на панели подсказки — игнорим
                    continue
                parts.append(f"x={x:.1f},y={y_adj:.1f}")

            if not parts:
                # Фолбэк: оператор клацал только в верхней части. Возвращаем как есть
                # без сдвига — пусть вызывающий код пытается.
                parts = [
                    f"x={float(c.get('x', 0)):.1f},y={float(c.get('y', 0)):.1f}"
                    for c in coords if 'x' in c and 'y' in c
                ]
            if not parts:
                return None

            response_str = 'coordinates:' + ';'.join(parts)
            logger.info(f"✅ 2captcha coords (translated): {response_str}")
            return {'status': 1, 'response': response_str}

        except Exception as e:
            logger.error(f"2captcha solve_smart_captcha exception: {e}")
            import traceback
            traceback.print_exc()
            return None


def create_twocaptcha_solver(api_key: Optional[str] = None) -> Optional[TwoCaptchaSolver]:
    """Фабрика. Возвращает None если ключ не задан — чтобы вызывающий код мог пропустить fallback."""
    if not api_key:
        try:
            from app.config import settings
            api_key = getattr(settings, 'two_captcha_api_key', '') or ''
        except Exception:
            import os
            api_key = os.getenv('YANDEX_BOT_TWO_CAPTCHA_API_KEY', '')
    api_key = (api_key or '').strip()
    if not api_key:
        return None
    return TwoCaptchaSolver(api_key)
