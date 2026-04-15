"""Test that add_init_script fingerprint injection works."""
import sys, os, time, json
sys.path.insert(0, "/app")

from core.browser_manager import BrowserManager
from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile

db = SessionLocal()
profile_obj = db.query(BrowserProfile).filter(BrowserProfile.name == "Profile-25984").first()
if not profile_obj:
    print("Profile not found!")
    sys.exit(1)

# Build profile_data same as yandex_search does
from core.profile_generator import ProfileGenerator
pg = ProfileGenerator()
pdata = pg.generate_profile("Profile-25984", is_mobile=False)
pdata.update({
    "user_agent": profile_obj.user_agent,
    "viewport": {"width": profile_obj.viewport_width, "height": profile_obj.viewport_height},
    "timezone": profile_obj.timezone or "Europe/Moscow",
    "language": "ru-RU",
    "platform": profile_obj.platform or "Win32",
})

# Extract webgl
import json as _json
if profile_obj.webgl_fingerprint:
    wgl = _json.loads(profile_obj.webgl_fingerprint) if isinstance(profile_obj.webgl_fingerprint, str) else profile_obj.webgl_fingerprint
    if wgl and isinstance(wgl, dict):
        pdata["webgl_fingerprint"] = wgl

# Extract screen_fingerprint fields INCLUDING screen
if profile_obj.screen_fingerprint and isinstance(profile_obj.screen_fingerprint, dict):
    for k in ("screen", "css_media", "feature_flags", "audio_properties", "speech_voices", "sensor",
              "connection_info", "storage_quota", "heap_size", "system_colors", "system_fonts",
              "codecs", "keyboard_layout", "fonts"):
        if k in profile_obj.screen_fingerprint:
            pdata[k] = profile_obj.screen_fingerprint[k]

print(f"screen in pdata: {pdata.get('screen')}")
print(f"connection in pdata: {pdata.get('connection_info')}")
print(f"device_memory in pdata: {pdata.get('device_memory')}")
print(f"hardware_concurrency in pdata: {pdata.get('hardware_concurrency')}")
print(f"webgl vendor: {pdata.get('webgl_fingerprint', {}).get('unmaskedVendor', 'N/A')}")

bm = BrowserManager()
try:
    bid = bm.create_browser_session(pdata, None)
    d = bm.active_browsers[bid]
    
    # Navigate to example.com first to trigger init script
    d.get("http://example.com")
    time.sleep(2)
    
    # Now check fingerprint overrides
    result = d.execute_script("""
        var c = document.createElement('canvas');
        var gl = c.getContext('webgl');
        var ext = gl ? gl.getExtension('WEBGL_debug_renderer_info') : null;
        return {
            vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : 'no_ext',
            renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'no_ext',
            screen_w: screen.width,
            screen_h: screen.height,
            avail_w: screen.availWidth,
            avail_h: screen.availHeight,
            inner_w: window.innerWidth,
            inner_h: window.innerHeight,
            outer_w: window.outerWidth,
            outer_h: window.outerHeight,
            device_memory: navigator.deviceMemory,
            hw_concurrency: navigator.hardwareConcurrency,
            webdriver: navigator.webdriver,
            connection: navigator.connection ? navigator.connection.effectiveType : 'N/A',
            platform: navigator.platform,
            getParam_native: (function() {
                try {
                    var c2 = document.createElement('canvas');
                    var gl2 = c2.getContext('webgl');
                    return gl2.getParameter.toString().includes('[native code]');
                } catch(e) { return 'error: ' + e.message; }
            })()
        };
    """)
    
    print("\n=== FINGERPRINT CHECK ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
    
    # Check if WebGL is spoofed
    if "SwiftShader" in str(result.get("renderer", "")):
        print("\nFAIL: Still showing SwiftShader!")
    elif "NVIDIA" in str(result.get("renderer", "")):
        print("\nSUCCESS: WebGL renderer spoofed to NVIDIA!")
    else:
        print(f"\nUnknown renderer: {result.get('renderer')}")
    
    if result.get("device_memory") and result["device_memory"] > 0:
        print(f"OK: deviceMemory works: {result['device_memory']}")
    else:
        print(f"FAIL: deviceMemory still null/0")
        
    if result.get("screen_w") != result.get("inner_w"):
        print(f"OK: screen != viewport: {result['screen_w']}x{result['screen_h']} vs {result['inner_w']}x{result['inner_h']}")
    else:
        print(f"FAIL: screen == viewport: {result['screen_w']}x{result['screen_h']}")
        
finally:
    try:
        bm.close_browser(bid)
    except:
        pass
    db.close()
