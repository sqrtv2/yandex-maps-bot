import sys, time, json
sys.path.insert(0, "/app")
from core.browser_manager import BrowserManager
from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile

db = SessionLocal()
bp = db.query(BrowserProfile).filter(BrowserProfile.is_active == True).first()

profile_data = {
    "name": bp.name,
    "user_agent": bp.user_agent,
    "viewport": {"width": bp.viewport_width, "height": bp.viewport_height},
    "timezone": bp.timezone, "language": bp.language, "platform": bp.platform,
    "canvas_fingerprint": bp.canvas_fingerprint or "",
    "audio_fingerprint": bp.audio_fingerprint or "",
}
if bp.webgl_fingerprint:
    profile_data["webgl_fingerprint"] = json.loads(bp.webgl_fingerprint) if isinstance(bp.webgl_fingerprint, str) else bp.webgl_fingerprint
if bp.screen_fingerprint:
    sf = json.loads(bp.screen_fingerprint) if isinstance(bp.screen_fingerprint, str) else bp.screen_fingerprint
    for k in ("screen","hardware_concurrency","device_memory","max_touch_points","do_not_track",
              "css_media","feature_flags","audio_properties","speech_voices","connection_info",
              "storage_quota","heap_size","system_colors","system_fonts","codecs","keyboard_layout",
              "fonts","webgpu_fingerprint","sensor"):
        if k in sf:
            profile_data[k] = sf[k]

print(f"Profile: {bp.name}")
print(f"Expected WebGL: {profile_data.get('webgl_fingerprint',{}).get('unmaskedRenderer','?')[:60]}")

bm = BrowserManager()
bid = None
try:
    bid = bm.create_browser_session(profile_data, None)
    d = bm.active_browsers[bid]
    
    # Check if addScriptToEvaluateOnNewDocument actually works
    print("\n=== Navigating to test init scripts ===")
    
    # Navigate to a REAL URL so init scripts fire
    d.set_page_load_timeout(15)
    try:
        d.get("https://httpbin.org/html")
    except Exception as nav_err:
        print(f"Navigation error (may be timeout): {str(nav_err)[:80]}")
    time.sleep(2)
    
    # Diagnostic 1: Is getParameter patched?
    r1 = d.execute_script("return WebGLRenderingContext.prototype.getParameter.toString().substring(0, 100)")
    print(f"\ngetParameter.toString(): {r1}")
    
    # Diagnostic 2: What does getParameter(37445/37446) return?
    r2 = d.execute_script("""
        var c = document.createElement('canvas');
        var gl = c.getContext('webgl');
        if (!gl) return {error: 'no webgl context'};
        return {
            vendor_37445: gl.getParameter(37445),
            renderer_37446: gl.getParameter(37446),
            vendor_7936: gl.getParameter(7936),
            renderer_7937: gl.getParameter(7937)
        };
    """)
    print(f"getParameter results: {json.dumps(r2, indent=2)}")
    
    # Diagnostic 3: Check via extension object
    r3 = d.execute_script("""
        var c = document.createElement('canvas');
        var gl = c.getContext('webgl');
        if (!gl) return {error: 'no gl'};
        var dbg = gl.getExtension('WEBGL_debug_renderer_info');
        if (!dbg) return {error: 'no WEBGL_debug_renderer_info extension'};
        return {
            UNMASKED_VENDOR_CONST: dbg.UNMASKED_VENDOR_WEBGL,
            UNMASKED_RENDERER_CONST: dbg.UNMASKED_RENDERER_WEBGL,
            via_const_vendor: gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL),
            via_const_renderer: gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)
        };
    """)
    print(f"Via extension: {json.dumps(r3, indent=2)}")
    
    # Diagnostic 4: deviceMemory and hardwareConcurrency
    r4 = d.execute_script("return {deviceMemory: navigator.deviceMemory, hwConcurrency: navigator.hardwareConcurrency}")
    print(f"Navigator: {json.dumps(r4, indent=2)}")
    
    # Diagnostic 5: Check if _nativeFuncs map exists (our stealth script marker)
    r5 = d.execute_script("return typeof _nativeFuncs !== 'undefined' ? 'stealth script LOADED' : 'stealth script NOT loaded'")
    print(f"Stealth status: {r5}")

finally:
    if bid:
        try:
            bm.close_browser_session(bid)
        except:
            pass
    db.close()
    print("\nDone!")
