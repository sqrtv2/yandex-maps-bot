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
    "is_mobile": bp.is_mobile,
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

wgl = profile_data.get('webgl_fingerprint', {})
print(f"Profile: {bp.name}")
print(f"Expected WebGL renderer: {wgl.get('unmaskedRenderer','?')}")
print(f"Expected WebGL vendor: {wgl.get('unmaskedVendor','?')}")
print(f"Expected deviceMemory: {profile_data.get('device_memory','?')}")
print(f"Expected maxTouchPoints: {profile_data.get('max_touch_points','?')}")
print(f"Expected hardwareConcurrency: {profile_data.get('hardware_concurrency','?')}")

bm = BrowserManager()
bid = None
try:
    bid = bm.create_browser_session(profile_data, None)
    d = bm.active_browsers[bid]

    # Navigate to a simple page to trigger init scripts
    print("\n=== Navigating to example.com ===")
    d.get("https://example.com")
    time.sleep(2)

    # Now check fingerprint on the NAVIGATED page
    print("\n=== Fingerprint AFTER navigation (init scripts should be active) ===")
    fp = d.execute_script("""
        var r = {};
        r.userAgent = navigator.userAgent;
        r.platform = navigator.platform;
        r.languages = JSON.stringify(navigator.languages);
        r.hardwareConcurrency = navigator.hardwareConcurrency;
        r.deviceMemory = navigator.deviceMemory;
        r.maxTouchPoints = navigator.maxTouchPoints;
        r.webdriver = navigator.webdriver;
        r.doNotTrack = navigator.doNotTrack;

        // WebGL
        try {
            var c = document.createElement('canvas');
            var gl = c.getContext('webgl');
            if (gl) {
                var ext = gl.getExtension('WEBGL_debug_renderer_info');
                if (ext) {
                    r.webglVendor = gl.getParameter(ext.UNMASKED_VENDOR_WEBGL);
                    r.webglRenderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
                }
                r.glVersion = gl.getParameter(gl.VERSION);
                r.shadingLang = gl.getParameter(gl.SHADING_LANGUAGE_VERSION);
                r.glVendor = gl.getParameter(gl.VENDOR);
                r.glRenderer = gl.getParameter(gl.RENDERER);
                r.maxTextureSize = gl.getParameter(gl.MAX_TEXTURE_SIZE);
                r.maxViewportW = gl.getParameter(gl.MAX_VIEWPORT_DIMS)[0];
                r.maxViewportH = gl.getParameter(gl.MAX_VIEWPORT_DIMS)[1];
            }
        } catch(e) { r.webglError = e.message; }

        // WebGL2
        try {
            var c2 = document.createElement('canvas');
            var gl2 = c2.getContext('webgl2');
            r.webgl2Available = !!gl2;
            if (gl2) {
                var ext2 = gl2.getExtension('WEBGL_debug_renderer_info');
                if (ext2) {
                    r.webgl2Vendor = gl2.getParameter(ext2.UNMASKED_VENDOR_WEBGL);
                    r.webgl2Renderer = gl2.getParameter(ext2.UNMASKED_RENDERER_WEBGL);
                }
            }
        } catch(e) {}

        // Screen
        r.screenW = screen.width;
        r.screenH = screen.height;
        r.availW = screen.availWidth;
        r.availH = screen.availHeight;
        r.colorDepth = screen.colorDepth;
        r.pixelRatio = window.devicePixelRatio;

        // Canvas fingerprint
        try {
            var cv = document.createElement('canvas');
            cv.width = 200; cv.height = 50;
            var ctx = cv.getContext('2d');
            ctx.textBaseline = 'top';
            ctx.font = '14px Arial';
            ctx.fillStyle = '#f60';
            ctx.fillRect(125, 1, 62, 20);
            ctx.fillStyle = '#069';
            ctx.fillText('Cwm fjord', 2, 15);
            r.canvasHash = cv.toDataURL().length;
        } catch(e) {}

        // Timezone
        r.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

        // AudioContext
        try {
            var ac = new (window.AudioContext || window.webkitAudioContext)();
            r.audioSampleRate = ac.sampleRate;
            r.audioMaxChannels = ac.destination.maxChannelCount;
            ac.close();
        } catch(e) {}

        // Connection
        if (navigator.connection) {
            r.connectionType = navigator.connection.effectiveType;
            r.connectionDownlink = navigator.connection.downlink;
        }

        // Plugins
        r.pluginsCount = navigator.plugins.length;
        r.mimeTypesCount = navigator.mimeTypes.length;

        return r;
    """)

    print("\n--- RESULTS ---")
    for k, v in sorted(fp.items()):
        marker = ""
        if k == "webglRenderer":
            expected = wgl.get('unmaskedRenderer', '')
            marker = " ✅" if v == expected else f" ❌ (expected: {expected})"
        elif k == "webglVendor":
            expected = wgl.get('unmaskedVendor', '')
            marker = " ✅" if v == expected else f" ❌ (expected: {expected})"
        elif k == "deviceMemory" and v is None:
            marker = " ❌ (not spoofed!)"
        elif k == "webdriver" and v:
            marker = " ❌ (DETECTED!)"
        elif k == "maxTouchPoints" and v == 0:
            marker = " ⚠️ (0 for mobile?)"
        print(f"  {k}: {v}{marker}")

    # Check for webdriver-related properties
    print("\n--- Automation detection ---")
    auto = d.execute_script("""
        var r = {};
        r.webdriver = navigator.webdriver;
        r.hasChromeCDC = !!window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        r.hasChromeCDC2 = !!window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        r.chromeRuntime = !!window.chrome && !!window.chrome.runtime;
        r.chromeApp = !!window.chrome && !!window.chrome.app;
        r.notificationPermission = typeof Notification !== 'undefined' ? Notification.permission : 'N/A';
        r.hasPlugins = navigator.plugins.length > 0;
        r.pluginNames = Array.from(navigator.plugins).map(p => p.name).join(', ');
        return r;
    """)
    for k, v in sorted(auto.items()):
        print(f"  {k}: {v}")

    print("\n=== DONE ===")

finally:
    if bid:
        try:
            bm.close_browser(bid)
        except:
            pass
    db.close()
