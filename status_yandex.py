#!/usr/bin/env python3
"""Show system status for Yandex Maps visits"""
from app.database import SessionLocal
from app.models import BrowserProfile, ProxyServer

with SessionLocal() as db:
    total = db.query(BrowserProfile).count()
    warmed = db.query(BrowserProfile).filter(BrowserProfile.warmup_completed == True).count()
    warming = db.query(BrowserProfile).filter(BrowserProfile.status == 'warming_up').count()
    proxies = db.query(ProxyServer).filter(ProxyServer.is_active == True).all()
    
    print("=" * 60)
    print("🎯 ГОТОВО К ПОСЕЩЕНИЮ ЯНДЕКС КАРТ")
    print("=" * 60)
    print()
    print("📊 Статус системы:")
    print(f"   ✅ Прогретых профилей: {warmed}/{total}")
    print(f"   🔄 В процессе прогрева: {warming}")
    print(f"   🌐 Активных прокси: {len(proxies)}")
    print()
    
    if proxies:
        print("🔒 Прокси:")
        for p in proxies:
            print(f"   • {p.name}: {p.host}:{p.port} ({p.proxy_type})")
    print()
    
    print("🚀 Команды для запуска:")
    print()
    print("   # Посетить один URL")
    print("   python3 visit_yandex.py 'https://yandex.ru/maps/org/...'")
    print()
    print("   # Посетить с конкретным профилем")
    print("   python3 visit_yandex.py 'https://yandex.ru/maps/org/...' --profile 1")
    print()
    print("   # Посетить несколько URL")
    print("   python3 visit_yandex.py --multiple 'URL1' 'URL2' 'URL3'")
    print()
    print("   # Интерактивный режим")
    print("   python3 visit_yandex.py")
    print()
    print("📈 Мониторинг:")
    print("   • Web UI: http://127.0.0.1:8000/tasks")
    print("   • Логи: tail -f logs/celery.log")
    print()
