#!/usr/bin/env python3
"""Test WebGL and screen spoofing on about:blank."""
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

JS = """
var c = document.createElement("canvas");
var gl = c.getContext("webgl");
if (!gl) return "NO WEBGL CONTEXT";
var ext = gl.getExtension("WEBGL_debug_renderer_info");
var result = {};
if (ext) {
    result.vendor = gl.getParameter(ext.UNMASKED_VENDOR_WEBGL);
    result.renderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
} else {
    result.vendor = "NO_EXT";
    result.renderer = "NO_EXT";
}
result.screen_w = screen.width;
result.screen_h = screen.height;
result.avail_w = screen.availWidth;
result.avail_h = screen.availHeight;
result.inner_w = window.innerWidth;
result.inner_h = window.innerHeight;
result.outer_w = window.outerWidth;
result.outer_h = window.outerHeight;
result.dpr = window.devicePixelRatio;
result.webdriver = navigator.webdriver;
result.plugins = navigator.plugins.length;
result.platform = navigator.platform;
result.hw_concurrency = navigator.hardwareConcurrency;
result.device_memory = navigator.deviceMemory;
result.connection = navigator.connection ? navigator.connection.effectiveType : "N/A";
result.languages = navigator.languages;
result.chrome_runtime = !!(window.chrome && window.chrome.runtime);
return result;
"""

r = d.execute_script(JS)
print(json.dumps(r, indent=2, ensure_ascii=False))
bm.close_browser_session(bid)
