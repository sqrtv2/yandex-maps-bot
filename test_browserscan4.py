import sys, time, json, os
sys.path.insert(0, "/app")
from core.browser_manager import BrowserManager
from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile

db = SessionLocal()
# Use a DESKTOP profile for better browserscan rendering
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

    # First collect our own JS fingerprint (fast, no network)
    print("\n=== Self-check fingerprint via JS ===")
    fp_data = d.execute_script("""
        var result = {};
        result.userAgent = navigator.userAgent;
        result.platform = navigator.platform;
        result.languages = JSON.stringify(navigator.languages);
        result.hardwareConcurrency = navigator.hardwareConcurrency;
        result.deviceMemory = navigator.deviceMemory;
        result.maxTouchPoints = navigator.maxTouchPoints;
        result.webdriver = navigator.webdriver;
        try {
            var c = document.createElement('canvas');
            var gl = c.getContext('webgl');
            if (gl) {
                var ext = gl.getExtension('WEBGL_debug_renderer_info');
                if (ext) {
                    result.webglVendor = gl.getParameter(ext.UNMASKED_VENDOR_WEBGL);
                    result.webglRenderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
                }
                result.glVersion = gl.getParameter(gl.VERSION);
                result.shadingLang = gl.getParameter(gl.SHADING_LANGUAGE_VERSION);
            }
        } catch(e) { result.webglError = e.message; }
        result.screenWidth = screen.width;
        result.screenHeight = screen.height;
        result.availWidth = screen.availWidth;
        result.availHeight = screen.availHeight;
        result.colorDepth = screen.colorDepth;
        result.pixelRatio = window.devicePixelRatio;
        result.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        result.doNotTrack = navigator.doNotTrack;
        return result;
    """)
    for k, v in sorted(fp_data.items()):
        print(f"  {k}: {v}")

    # Now try browserscan
    print("\n=== Opening browserscan.net ===")
    d.set_page_load_timeout(60)
    try:
        d.get("https://www.browserscan.net/")
    except Exception as e:
        print(f"Navigation (timeout ok): {str(e)[:80]}")

    print("Waiting 25s for analysis...")
    time.sleep(25)

    SDIR = "/tmp/bscan"
    os.makedirs(SDIR, exist_ok=True)

    # Take screenshots safely
    for i, scroll_y in enumerate([0, 500, 1000, 1500, 2000]):
        try:
            d.execute_script(f"window.scrollTo(0, {scroll_y})")
            time.sleep(1)
            page.screenshot(path=f"/tmp/bscan/bs_{i}.png", full_page=False)
            print(f"Screenshot bs_{i}.png saved (scroll={scroll_y})")
        except Exception as e:
            print(f"Screenshot bs_{i} failed: {str(e)[:60]}")
            break

    # Save DOM
    try:
        dom = d.page_source
        with open("/tmp/bscan/browserscan_dom.html", "w", encoding="utf-8") as f:
            f.write(dom)
        print(f"DOM saved ({len(dom)} bytes)")
    except Exception as e:
        print(f"DOM save failed: {str(e)[:60]}")

    # Try WebGL page
    print("\n=== Opening browserscan WebGL page ===")
    try:
        d.get("https://www.browserscan.net/webgl")
    except Exception:
        pass
    time.sleep(15)

    for i, scroll_y in enumerate([0, 500, 1000]):
        try:
            d.execute_script(f"window.scrollTo(0, {scroll_y})")
            time.sleep(1)
            page.screenshot(path=f"/tmp/bscan/bs_webgl_{i}.png", full_page=False)
            print(f"Screenshot bs_webgl_{i}.png saved")
        except Exception as e:
            print(f"Screenshot bs_webgl_{i} failed: {str(e)[:60]}")
            break

    try:
        dom2 = d.page_source
        with open("/tmp/bscan/bs_webgl_dom.html", "w", encoding="utf-8") as f:
            f.write(dom2)
        print(f"WebGL DOM saved ({len(dom2)} bytes)")
    except Exception as e:
        print(f"WebGL DOM save failed: {str(e)[:60]}")

    print("\n=== DONE ===")

finally:
    if bid:
        try:
            bm.close_browser(bid)
        except:
            pass
    db.close()
