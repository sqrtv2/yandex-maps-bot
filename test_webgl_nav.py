#!/usr/bin/env python3
"""Debug: check fingerprint AFTER navigation to real page."""
import sys, json
sys.path.insert(0, '/app')
from core.browser_manager import BrowserManager
from app.database import get_db_session
from app.models import BrowserProfile, ProxyServer

with get_db_session() as db:
    p = db.query(BrowserProfile).filter(BrowserProfile.is_active == True).first()
    proxy = db.query(ProxyServer).filter(ProxyServer.is_active == True).first()
    pd = {
        'name': p.name, 'user_agent': p.user_agent,
        'viewport': {'width': p.viewport_width or 1366, 'height': p.viewport_height or 768},
        'timezone': p.timezone or 'Europe/Moscow',
        'webgl_fingerprint': json.loads(p.webgl_fingerprint) if p.webgl_fingerprint else {},
        'is_mobile': getattr(p, 'is_mobile', False),
        'screen': json.loads(p.screen) if hasattr(p, 'screen') and p.screen else {},
        'navigator': json.loads(p.navigator) if hasattr(p, 'navigator') and p.navigator else {},
        'canvas_fingerprint': getattr(p, 'canvas_fingerprint', None),
        'audio_fingerprint': getattr(p, 'audio_fingerprint', None),
        'fonts': json.loads(p.fonts) if hasattr(p, 'fonts') and p.fonts else [],
    }
    pxd = None
    if proxy:
        pxd = {'id': proxy.id, 'host': proxy.host, 'port': proxy.port,
                'proxy_type': proxy.proxy_type or 'http',
                'username': proxy.username, 'password': proxy.password}

bm = BrowserManager()
bid = bm.create_browser_session(pd, pxd)
d = bm.active_browsers[bid]

print("=== ON about:blank (before navigation) ===")
r = d.execute_script("""
var c = document.createElement("canvas");
var gl = c.getContext("webgl");
if (!gl) return "NO_GL";
var ext = gl.getExtension("WEBGL_debug_renderer_info");
if (!ext) return "NO_EXT";
return {vendor: gl.getParameter(37445), renderer: gl.getParameter(37446)};
""")
print(json.dumps(r, indent=2))

print("\n=== Navigating to example.com ===")
d.get("http://example.com")
import time; time.sleep(3)

r2 = d.execute_script("""
var c = document.createElement("canvas");
var gl = c.getContext("webgl");
if (!gl) return "NO_GL";
var ext = gl.getExtension("WEBGL_debug_renderer_info");
if (!ext) return "NO_EXT";
return {
    vendor: gl.getParameter(37445),
    renderer: gl.getParameter(37446),
    getParam_str: WebGLRenderingContext.prototype.getParameter.toString().substring(0, 50),
    screen_w: screen.width,
    screen_h: screen.height,
    avail_w: screen.availWidth,
    avail_h: screen.availHeight,
};
""")
print(json.dumps(r2, indent=2))

bm.close_browser_session(bid)
