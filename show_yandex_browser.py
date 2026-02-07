"""Запуск визита через Celery - браузер откроется на экране"""
from tasks.yandex_maps import visit_yandex_maps_profile_task

print("="*80)
print("🔍 ЗАПУСКАЕМ ВИЗИТ МЕДСЕМЬЯ ЧЕРЕЗ CELERY")
print("="*80)
print("")
print("✅ Браузер откроется на экране (headless=False в config)")
print("✅ Используется selenium-wire + HTTP прокси")
print("✅ Вы увидите что показывает Яндекс")
print("")
print("="*80)

result = visit_yandex_maps_profile_task.apply_async(
    args=[1, 'https://yandex.ru/maps/org/medsemya/1201821372'],
    kwargs={},
)

print(f"\n✅ Задача запущена: {result.id}")
print(f"   Profile: Profile-1")
print(f"   Прокси: mproxy.site:12138 (HTTP)")
print(f"   URL: https://yandex.ru/maps/org/medsemya/1201821372")
print("")
print("📺 Браузер должен появиться на экране через 10-15 секунд")
print("")
print("⏰ Мониторинг через 60 секунд:")
print(f"   python3 -c \"from celery.result import AsyncResult; from tasks.celery_app import app; r=AsyncResult('{result.id}', app=app); print('Status:', r.status); print('Info:', r.info)\"")
print("")
print("📄 Логи:")
print("   tail -f logs/celery.log | grep -E '(Successfully|Captcha|protection|ERR_)'")
