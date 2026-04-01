import requests, time
from app.database import get_db_session
from app.models import ProxyServer

with get_db_session() as db:
    proxies = db.query(ProxyServer).filter(ProxyServer.is_active == True, ProxyServer.is_working == True).all()
    print(f'Testing {len(proxies)} proxies...')

    for p in proxies:
        proxy_url = f'http://{p.username}:{p.password}@{p.host}:{p.port}'
        try:
            start = time.time()
            r = requests.get('https://yandex.ru/maps/', proxies={'https': proxy_url}, timeout=10,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/144.0.0.0 Safari/537.36'})
            elapsed = time.time() - start
            ok = r.status_code == 200 and len(r.text) > 1000
            status = 'OK' if ok else f'BAD(status={r.status_code},len={len(r.text)})'
            print(f'  {p.host}:{p.port} => {status} {elapsed:.1f}s')
            if not ok:
                p.is_working = False
        except Exception as e:
            err = str(e)[:60]
            print(f'  {p.host}:{p.port} => FAIL: {err}')
            p.is_working = False

    db.commit()
    working = db.query(ProxyServer).filter(ProxyServer.is_active == True, ProxyServer.is_working == True).count()
    print(f'Working proxies remaining: {working}')
