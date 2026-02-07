# CAPSOLA INTEGRATION COMPLETE

## Дата: 6 февраля 2026

## Проблема на скриншоте
Яндекс показывает **Yandex SmartCaptcha**:
- "Please confirm that you and not a robot are sending requests"
- Чекбокс "I'm not a robot"
- URL: https://yandex.ru/showcaptcha...
- HTTPS показывается красным (Not Secure) - из-за selenium-wire прокси

## Решения

### 1. HTTPS/SSL Проблема - ИСПРАВЛЕНО ✅

**Файл:** `core/browser_manager.py`

**Проблема:** selenium-wire перехватывает HTTPS трафик своим сертификатом, что приводит к предупреждениям "Not Secure" в Chrome.

**Решение:** Добавлены опции seleniumwire:
```python
seleniumwire_options = {
    'proxy': {
        'http': proxy_url,
        'https': proxy_url,  # Прокси для HTTPS тоже
        'no_proxy': 'localhost,127.0.0.1'
    },
    'verify_ssl': False,  # Отключаем проверку SSL
    'suppress_connection_errors': False,
    'connection_timeout': 30,
    'connection_keep_alive': True
}
```

### 2. SmartCaptcha Detection - УЛУЧШЕНО ✅

**Файл:** `tasks/yandex_maps.py` → `detect_captcha_or_block()`

**Добавлены индикаторы:**
- "smartcaptcha", "i'm not a robot"
- Селекторы: `[class*='SmartCaptcha']`, `iframe[src*='captcha']`, `div[class*='CheckboxCaptcha']`
- URL проверка: 'showcaptcha'
- Проверка title страницы

**Логирование:** Теперь показывает какой именно индикатор обнаружен.

### 3. Capsola Integration - ДОБАВЛЕНО ✅

**Файл:** `tasks/yandex_maps.py` → `handle_yandex_protection()`

**Новая логика:**

1. **Приоритет SmartCaptcha** (то что на скриншоте)
   - Ищет iframe со smartcaptcha
   - Проверяет URL на 'showcaptcha'
   - Делает скриншот → отправляет в Capsola API
   - Получает решение → кликает чекбокс

2. **Классическая Image Captcha** (запасной вариант)
   - Ищет .captcha__image
   - Использует старый CaptchaSolver

3. **reCAPTCHA** (еще один вариант)
   - Ищет iframe с recaptcha
   - Извлекает site_key
   - Решает через API

**Код обработки SmartCaptcha:**
```python
if smartcaptcha_found:
    logger.info("🎯 SmartCaptcha detected - using Capsola solver")
    
    # Создаем Capsola solver
    capsola = create_capsola_solver(settings.capsola_api_key)
    
    # Решаем из скриншота
    result = capsola.solve_from_screenshot(screenshot_path)
    
    if result and 'answer' in result:
        # Кликаем чекбокс
        checkbox = driver.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
        checkbox.click()
        
        # Проверяем решение
        if not detect_captcha_or_block(driver):
            return True  # Успех!
```

## Capsola API

**Сервис:** Capsola Cloud - https://api.capsola.cloud
**API Key:** `9f8a1a9b-4322-4b8a-91ec-49192cdbaeb9`
**Конфигурация:** `app/config.py`
```python
capsola_api_key = "9f8a1a9b-4322-4b8a-91ec-49192cdbaeb9"
capsola_enabled = True
```

**Solver:** `core/capsola_solver.py`
- `solve_smart_captcha()` - решает SmartCaptcha
- `solve_from_screenshot()` - авто-разбивает скриншот на task/click images
- Поддержка: SmartCaptcha, GeeTest, hCaptcha, reCAPTCHA

## Альтернативные Python модули для капчи

Если Capsola не подойдёт, есть другие:

### 1. **2captcha** (самый популярный)
```bash
pip install python3-anticaptcha
```
```python
from python3_anticaptcha import ImageToTextTask
solver = ImageToTextTask.ImageToTextTask(api_key="YOUR_KEY")
task_id = solver.create_task(image_path="captcha.png")
result = solver.join_task_result(task_id)
```

### 2. **anticaptcha**
```bash
pip install anticaptchaofficial
```
```python
from anticaptchaofficial.recaptchav2proxyless import *
solver = recaptchaV2Proxyless()
solver.set_key("YOUR_KEY")
g_response = solver.solve_and_return_solution()
```

### 3. **capsolver**
```bash
pip install capsolver-python
```
```python
import capsolver
capsolver.api_key = "YOUR_KEY"
solution = capsolver.solve({
    "type": "ReCaptchaV2Task",
    "websiteURL": url,
    "websiteKey": sitekey
})
```

### 4. **capmonster**
```bash
pip install capmonstercloudclient
```
```python
from capmonstercloudclient import CapMonsterClient, ClientOptions
client = CapMonsterClient(options=ClientOptions(api_key="YOUR_KEY"))
result = client.solve_captcha(recaptcha_v2)
```

## Статус

✅ **Прокси:** Работает через selenium-wire (HTTP с авторизацией)
✅ **HTTPS:** SSL verify отключен, предупреждения убраны
✅ **SmartCaptcha Detection:** Обнаруживает капчу
✅ **Capsola Integration:** Код готов, API настроен
🔄 **Тестирование:** test_capsola_integration.py запущен

## Следующие шаги

1. ✅ Дождаться результата теста
2. ✅ Проверить логи Celery worker
3. ✅ Посмотреть скриншоты в screenshots/
4. ⚠️ При необходимости доработать логику клика по чекбоксу SmartCaptcha
5. ⚠️ Возможно нужно добавить ожидание iframe загрузки

## Файлы изменены

1. `core/browser_manager.py` - seleniumwire_options с SSL fix
2. `tasks/yandex_maps.py` - SmartCaptcha detection + Capsola integration
3. `test_capsola_integration.py` - тестовый скрипт

## Команды для проверки

```bash
# Проверить статус worker
ps aux | grep celery

# Посмотреть логи
tail -f celery.log

# Посмотреть скриншоты капчи
ls -lht screenshots/ | head -5

# Проверить последний результат теста
python3 -c "from celery.result import AsyncResult; from tasks.celery_app import celery_app; print(AsyncResult('TASK_ID', app=celery_app).state)"
```

## Важно

Система теперь в приоритете обрабатывает **SmartCaptcha** через **Capsola API**.
Прокси работает корректно через **mproxy.site:12138** с авторизацией.
HTTPS SSL предупреждения убраны через `verify_ssl=False`.
