import sys, time, json, os
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
print(f"UA: {bp.user_agent[:80]}")
print(f"Expected WebGL renderer: {profile_data.get('webgl_fingerprint',{}).get('unmaskedRenderer','?')[:80]}")

bm = BrowserManager()
bid = None
try:
    bid = bm.create_browser_session(profile_data, None)
    d = bm.active_browsers[bid]
    page = d._page

    print("\n=== Opening browserscan.net ===")
    d.set_page_load_timeout(45)
    try:
        d.get("https://www.browserscan.net/")
    except Exception as e:
        print(f"Navigation (may be timeout, continuing): {str(e)[:80]}")

    # Wait for page to analyze
    print("Waiting 20s for browserscan to analyze...")
    time.sleep(20)

    # Screenshot 1 - main page  
    os.makedirs("/app/screenshots", exist_ok=True)
    page.screenshot(path="/app/screenshots/browserscan_main.png", full_page=False)
    print("Screenshot 1 saved: /app/screenshots/browserscan_main.png")

    # Scroll down to see more results
    d.execute_script("window.scrollTo(0, 600)")
    time.sleep(2)
    page.screenshot(path="/app/screenshots/browserscan_scroll1.png", full_page=False)
    print("Screenshot 2 saved: /app/screenshots/browserscan_scroll1.png")

    d.execute_script("window.scrollTo(0, 1200)")
    time.sleep(2)
    page.screenshot(path="/app/screenshots/browserscan_scroll2.png", full_page=False)
    print("Screenshot 3 saved: /app/screenshots/browserscan_scroll2.png")

    d.execute_script("window.scrollTo(0, 1800)")
    time.sleep(2)
    page.screenshot(path="/app/screenshots/browserscan_scroll3.png", full_page=False)
    print("Screenshot 4 saved: /app/screenshots/browserscan_scroll3.png")

    # Save DOM
    dom = d.page_source
    with open("/app/screenshots/browserscan_dom.html", "w", encoding="utf-8") as f:
        f.write(dom)
    print(f"DOM saved: /app/screenshots/browserscan_dom.html ({len(dom)} bytes)")

    # Extract key values via JS
    print("\n=== Key fingerprint values from JS ===")
    fp_data = d.execute_script("""
        var result = {};
        // Basic
        result.userAgent = navigator.userAgent;
        result.platform = navigator.platform;
        result.languages = navigator.languages;
        result.hardwareConcurrency = navigator.hardwareConcurrency;
        result.deviceMemory = navigator.deviceMemory;
        result.maxTouchPoints = navigator.maxTouchPoints;
        result.webdriver = navigator.webdriver;
        // WebGL
        try {
            var c = document.createElement('canvas');
            var gl = c.getContext('webgl');
            if (gl) {
                var ext = gl.getExtension('WEBGL_debug_renderer_info');
                if (ext) {
                    result.webglVendor = gl.getParameter(ext.UNMASKED_VENDOR_WEBGL);
                    result.webglRenderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
                }
            }
        } catch(e) { result.webglError = e.message; }
        // Screen
        result.screenWidth = screen.width;
        result.screenHeight = screen.height;
        result.colorDepth = screen.colorDepth;
        result.pixelRatio = window.devicePixelRatio;
        // Timezone
        result.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        return result;
    """)
    for k, v in fp_data.items():
        print(f"  {k}: {v}")

    # Try navigating to WebGL check page
    print("\n=== Opening browserscan WebGL page ===")
    try:
        d.get("https://www.browserscan.net/webgl")
    except Exception:
        pass
    time.sleep(10)
    page.screenshot(path="/app/screenshots/browserscan_webgl.png", full_page=False)
    print("Screenshot WebGL saved: /app/screenshots/browserscan_webgl.png")

    d.execute_script("window.scrollTo(0, 600)")
    time.sleep(2)
    page.screenshot(path="/app/screenshots/browserscan_webgl2.png", full_page=False)
    print("Screenshot WebGL2 saved: /app/screenshots/browserscan_webgl2.png")

    # Save WebGL page DOM
    dom2 = d.page_source
    with open("/app/screenshots/browserscan_webgl_dom.html", "w", encoding="utf-8") as f:
        f.write(dom2)
    print(f"WebGL DOM saved ({len(dom2)} bytes)")

    print("\n=== DONE ===")

finally:
    if bid:
        try:
            bm.close_browser(bid)
        except:
            pass
    db.close()
