"""Проверяем последний скриншот из Celery задачи"""
import os
import glob
from datetime import datetime

screenshots_dir = '/Users/sqrtv2/Project/PF/screenshots'

print("="*80)
print("📸 ПОСЛЕДНИЕ СКРИНШОТЫ")
print("="*80)
print("")

# Получаем все скриншоты
screenshots = glob.glob(f"{screenshots_dir}/*.png")

if not screenshots:
    print("❌ Скриншоты не найдены")
else:
    # Сортируем по времени изменения
    screenshots.sort(key=os.path.getmtime, reverse=True)
    
    print(f"Найдено скриншотов: {len(screenshots)}")
    print("")
    print("📋 Последние 5 скриншотов:")
    print("")
    
    for i, screenshot in enumerate(screenshots[:5]):
        mtime = os.path.getmtime(screenshot)
        dt = datetime.fromtimestamp(mtime)
        size = os.path.getsize(screenshot)
        name = os.path.basename(screenshot)
        
        print(f"{i+1}. {name}")
        print(f"   Время: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Размер: {size:,} bytes")
        print(f"   Путь: {screenshot}")
        print("")
    
    # Показываем самый новый
    latest = screenshots[0]
    print("="*80)
    print(f"📸 САМЫЙ НОВЫЙ СКРИНШОТ:")
    print(f"   {latest}")
    print("")
    print(f"Откройте его чтобы увидеть что показывает Яндекс:")
    print(f"   open '{latest}'")
    print("="*80)
