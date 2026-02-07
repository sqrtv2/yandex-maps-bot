#!/usr/bin/env python3
"""Add SOCKS5 proxy to database"""
from app.database import SessionLocal
from app.models import ProxyServer

proxy_data = {
    'name': 'SOCKS5-Proxy-1',
    'host': '185.234.59.13',
    'port': 12138,
    'username': 'Hes9yF',
    'password': 'zAU2vaEUf4TU',
    'proxy_type': 'socks5',
    'is_active': True
}

with SessionLocal() as db:
    existing = db.query(ProxyServer).filter(
        ProxyServer.host == proxy_data['host'],
        ProxyServer.port == proxy_data['port']
    ).first()
    
    if existing:
        print(f"⚠️  Прокси уже существует: {existing.name}")
        print(f"   ID: {existing.id}")
        print(f"   Адрес: {existing.host}:{existing.port}")
    else:
        proxy = ProxyServer(**proxy_data)
        db.add(proxy)
        db.commit()
        db.refresh(proxy)
        print("✅ SOCKS5 прокси добавлен!")
        print(f"   ID: {proxy.id}")
        print(f"   Имя: {proxy.name}")
        print(f"   Адрес: {proxy.host}:{proxy.port}")
        print(f"   Тип: {proxy.proxy_type}")
