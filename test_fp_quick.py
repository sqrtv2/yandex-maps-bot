import sys, os, time, json
sys.path.insert(0, "/app")

from core.browser_manager import BrowserManager
from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile

db = SessionLocal()

# Grab any existing profile and build profile_data dict
bp = db.query(BrowserProfile).filter(BrowserProfile.is_active == True).first()
print(f"Using profile: {bp.name} (id={bp.id})")
print(f"UA: {bp.user_agent[:100]}")

profile_data = {
    "name": bp.name,
    "user_agent": bp.user_agent,
    "viewport": {"width": bp.viewport_width, "height": bp.viewport_height},
    "timezone": bp.timezone,
    "language": bp.language,
    "platform": bp.platform,
    "canvas_fingerprint": bp.canvas_fingerprint or "",
    "audio_fingerprint": bp.audio_fingerprint or "",
    "profile_dir": f"/app/profiles/{bp.name}",
}
if bp.webgl_fingerprint:
    profile_data["webgl_fingerprint"] = json.loads(bp.webgl_fingerprint) if isinstance(bp.webgl_fingerprint, str) else bp.webgl_fingerprint
if bp.screen_fingerprint:
    sf = json.loads(bp.screen_fingerprint) if isinstance(bp.screen_fingerprint, str) else bp.screen_fingerprint
    for k in ('screen', 'css_media', 'feature_flags', 'audio_properties', 'speech_voices', 'sensor',
              'connection_info', 'storage_quota', 'heap_size', 'system_colors',
              'system_fonts', 'codecs', 'keyboard_layout', 'fonts', 'webgpu_fingerprint',
              'hardware_concurrency', 'device_memory', 'max_touch_points', 'do_not_track'):
        if k in sf:
            profile_data[k] = sf[k]

bm = BrowserManager()
bid = None
try:
    bid = bm.create_browser_session(profile_data, None)
    d = bm.active_browsers[bid]

    # Navigate to trigger addScriptToEvaluateOnNewDocument  
    print("\n=== Loading page to check fingerprint ===")
    d.set_page_load_timeout(15)
    try:
        d.get("data:text/html,<h1>FP Test</h1>")
    except:
        pass
    time.sleep(1)

    # Collect fingerprint data directly via JS
    print("\n=== JS Fingerprint Check ===")
    fp_data = d.execute_script("""
        var r = {};
        r.userAgent = navigator.userAgent;
        r.platform = navigator.platform;
        r.webdriver = navigator.webdriver;
        r.languages = navigator.languages;
        r.hardwareConcurrency = navigator.hardwareConcurrency;
        r.deviceMemory = navigator.deviceMemory;
        r.maxTouchPoints = navigator.maxTouchPoints;
        r.doNotTrack = navigator.doNotTrack;
        r.cookieEnabled = navigator.cookieEnabled;
        r.pdfViewerEnabled = navigator.pdfViewerEnabled;

        // WebGL
        try {
            var c = document.createElement('canvas');
            var gl = c.getContext('webgl2') || c.getContext('webgl');
            if (gl) {
                var dbg = gl.getExtension('WEBGL_debug_renderer_info');
                r.webglVendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : 'no ext';
                r.webglRenderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'no ext';
            }
        } catch(e) { r.webglError = e.message; }

        // Screen
        r.screenWidth = screen.width;
        r.screenHeight = screen.height;
        r.colorDepth = screen.colorDepth;
        r.pixelRatio = window.devicePixelRatio;
        r.innerWidth = window.innerWidth;
        r.innerHeight = window.innerHeight;

        // Chrome-specific
        r.chrome = typeof window.chrome !== 'undefined';
        r.chromeRuntime = typeof window.chrome !== 'undefined' && typeof window.chrome.runtime !== 'undefined';

        // Permissions API
        r.notificationPermission = typeof Notification !== 'undefined' ? Notification.permission : 'N/A';

        // Plugin count
        r.pluginCount = navigator.plugins ? navigator.plugins.length : 0;

        // Connection
        if (navigator.connection) {
            r.connectionType = navigator.connection.effectiveType;
            r.connectionDownlink = navigator.connection.downlink;
            r.connectionRtt = navigator.connection.rtt;
        }

        // Automation detection
        r.webdriverProp = 'webdriver' in navigator;
        r.webdriverValue = navigator.webdriver;

        r.windowChrome = !!window.chrome;
        r.windowChromeApp = !!(window.chrome && window.chrome.app);
        r.windowChromeCsi = !!(window.chrome && window.chrome.csi);
        r.windowChromeLoadTimes = !!(window.chrome && window.chrome.loadTimes);

        return r;
    """)

    for k, v in sorted(fp_data.items()):
        print(f"  {k}: {v}")

    # Now open amiunique
    print("\n=== Opening amiunique.org ===")
    d.set_page_load_timeout(30)
    try:
        d.get("https://amiunique.org/fingerprint")
        time.sleep(8)
        d.save_screenshot("/app/screenshots/amiunique_fp.png")
        print("Screenshot saved: amiunique_fp.png")
        print(f"URL: {d.current_url}")
        print(f"Title: {d.title}")
    except Exception as e:
        print(f"amiunique failed: {e}")
        try:
            d.get("https://bot.sannysoft.com/")
            time.sleep(5)
            d.save_screenshot("/app/screenshots/sannysoft_fp.png")
            print("Fallback screenshot: sannysoft_fp.png")
        except Exception as e2:
            print(f"sannysoft also failed: {e2}")

finally:
    if bid:
        try:
            bm.close_browser_session(bid)
        except:
            pass
    db.close()
    print("\nDone!")
