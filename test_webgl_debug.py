#!/usr/bin/env python3
"""Debug: check if fingerprint script ran and what errors occurred."""
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

# Check 1: Does our stealth marker exist?
r1 = d.execute_script("return typeof window.__fpInjected !== 'undefined' ? window.__fpInjected : 'NOT_FOUND'")
print(f"Stealth marker: {r1}")

# Check 2: Is getParameter patched?
r2 = d.execute_script("""
var c = document.createElement("canvas");
var gl = c.getContext("webgl");
if (!gl) return "NO_GL";
// Check if getParameter is native or patched
var funcStr = gl.getParameter.toString();
return funcStr.substring(0, 100);
""")
print(f"getParameter.toString(): {r2}")

# Check 3: Check if WEBGL_debug_renderer_info extension is being intercepted
r3 = d.execute_script("""
var c = document.createElement("canvas");
var gl = c.getContext("webgl");
if (!gl) return "NO_GL";
var ext = gl.getExtension("WEBGL_debug_renderer_info");
if (!ext) return "NO_EXT";
return {
    UNMASKED_VENDOR: ext.UNMASKED_VENDOR_WEBGL,
    UNMASKED_RENDERER: ext.UNMASKED_RENDERER_WEBGL,
    vendor_val: gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
    renderer_val: gl.getParameter(ext.UNMASKED_RENDERER_WEBGL),
    vendor_by_num: gl.getParameter(37445),
    renderer_by_num: gl.getParameter(37446),
};
""")
print(f"WebGL ext check: {json.dumps(r3, indent=2)}")

# Check 4: Does WebGLRenderingContext.prototype have our patch?
r4 = d.execute_script("""
return WebGLRenderingContext.prototype.getParameter.toString().substring(0, 200);
""")
print(f"Prototype getParameter: {r4}")

# Check 5: Is the instance method different from prototype?
r5 = d.execute_script("""
var c = document.createElement("canvas");
var gl = c.getContext("webgl");
if (!gl) return "NO_GL";
return {
    instanceSame: gl.getParameter === WebGLRenderingContext.prototype.getParameter,
    instanceStr: gl.getParameter.toString().substring(0, 100),
    protoStr: WebGLRenderingContext.prototype.getParameter.toString().substring(0, 100)
};
""")
print(f"Instance vs Prototype: {json.dumps(r5, indent=2)}")

# Check 6: Screen overrides
r6 = d.execute_script("""
return {
    screen_w: screen.width,
    screen_h: screen.height,
    avail_w: screen.availWidth,
    avail_h: screen.availHeight,
    inner_w: window.innerWidth,
    inner_h: window.innerHeight,
    screen_desc_w: Object.getOwnPropertyDescriptor(Screen.prototype, 'width'),
    screen_has_getter: Object.getOwnPropertyDescriptor(Screen.prototype, 'width') ? 'has getter' : 'default'
};
""")
print(f"Screen: {json.dumps(r6, indent=2, default=str)}")

bm.close_browser_session(bid)
