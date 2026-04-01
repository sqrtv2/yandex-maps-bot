import requests, time
from app.database import get_db_session
from app.models import ProxyServer

with get_db_session() as db:
    proxies = db.query(ProxyServer).filter(
        ProxyServer.is_active == True, ProxyServer.is_working == True
    ).all()
    for p in proxies:
        proxy_url = f'http://{p.username}:{p.password}@{p.host}:{p.port}'
        try:
            start = time.time()
            r = requests.get(
                'https://yandex.ru/maps/',
                proxies={'https': proxy_url},
                timeout=10,
                headers={'User-Agent': 'Mozilla/5.0 Chrome/144.0.0.0 Safari/537.36'},
            )
            elapsed = time.time() - start
            ok = r.status_code == 200 and len(r.text) > 1000
            label = 'OK' if ok else 'BAD'
            print(f'{p.host}:{p.port} => {label} {elapsed:.1f}s')
            if not ok:
                p.is_working = False
                p.is_active = False
        except Exception as e:
            print(f'{p.host}:{p.port} => FAIL: {str(e)[:50]}')
            p.is_working = False
            p.is_active = False
    db.commit()
    alive = db.query(ProxyServer).filter(
        ProxyServer.is_active == True, ProxyServer.is_working == True
    ).count()
    print(f'Alive: {alive}')
