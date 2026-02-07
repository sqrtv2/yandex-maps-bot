"""Тест с видимым браузером для просмотра капчи Яндекса"""
from tasks.yandex_maps import visit_yandex_maps_profile_task

print("🚀 Запускаем тест с ВИДИМЫМ браузером")
print("   Браузер откроется на экране")
print("   Вы сможете увидеть капчу Яндекса")
print("")

result = visit_yandex_maps_profile_task.apply_async(
    args=[1, 'https://yandex.ru/maps/org/medsemya/1201821372'],
    kwargs={},
)

print(f"✅ Задача запущена: {result.id}")
print(f"   Profile: Profile-1 (HTTP прокси: mproxy.site:12138)")
print(f"   URL: https://yandex.ru/maps/org/medsemya/...")
print("")
print("📺 ВНИМАНИЕ: Браузер должен открыться на экране")
print("   Посмотрите какую капчу показывает Яндекс")
print("")
print("📋 Мониторинг через 40 секунд:")
print(f"   python3 -c \"from celery.result import AsyncResult; from tasks.celery_app import app; r=AsyncResult('{result.id}', app=app); print('Status:', r.status); print('Info:', r.info)\"")
