"""Create a NEW profile and test it on browserscan.net."""
import sys, os, time, json
sys.path.insert(0, "/app")

from core.browser_manager import BrowserManager
from core.profile_generator import ProfileGenerator
from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile
import json as _json

db = SessionLocal()

# Generate a brand new profile
pg = ProfileGenerator()
profile_name = "Profile-TEST-NEW"
p = pg.generate_profile(profile_name, is_mobile=False)

print(f"=== Generated profile: {profile_name} ===")
print(f"  UA: {p.get('user_agent', 'N/A')[:80]}...")
print(f"  Platform: {p.get('platform')}")
print(f"  Screen: {p.get('screen')}")
print(f"  Viewport: {p.get('viewport')}")
print(f"  WebGL vendor: {p.get('webgl_fingerprint', {}).get('unmaskedVendor', 'N/A')}")
print(f"  WebGL renderer: {p.get('webgl_fingerprint', {}).get('unmaskedRenderer', 'N/A')[:80]}")
print(f"  device_memory: {p.get('device_memory')}")
print(f"  hw_concurrency: {p.get('hardware_concurrency')}")
print(f"  connection: {p.get('connection_info', {}).get('effectiveType', 'N/A')}")

# Build screen_fingerprint JSON same as main.py does
viewport = p.get("viewport", {})
screen = p.get("screen", {})
screen_fp = {
    "screen": screen,
    "css_media": p.get("css_media", {}),
    "feature_flags": p.get("feature_flags", {}),
    "audio_properties": p.get("audio_properties", {}),
    "speech_voices": p.get("speech_voices", []),
    "connection_info": p.get("connection_info", {}),
    "storage_quota": p.get("storage_quota", 599720927232),
    "heap_size": p.get("heap_size", 4294705152),
    "system_colors": p.get("system_colors", {}),
    "system_fonts": p.get("system_fonts", []),
    "codecs": p.get("codecs", []),
    "keyboard_layout": p.get("keyboard_layout", []),
    "fonts": p.get("fonts", []),
    "webgpu_fingerprint": p.get("webgpu_fingerprint", {}),
    "hardware_concurrency": p.get("hardware_concurrency", 8),
    "device_memory": p.get("device_memory", 8),
    "max_touch_points": p.get("max_touch_points", 0),
    "do_not_track": p.get("do_not_track", False),
}
if p.get("sensor"):
    screen_fp["sensor"] = p["sensor"]

# Delete old test profile if exists
old = db.query(BrowserProfile).filter(BrowserProfile.name == profile_name).first()
if old:
    db.delete(old)
    db.commit()
    print(f"  Deleted old {profile_name}")

# Save to DB
profile_obj = BrowserProfile(
    name=profile_name,
    user_agent=p["user_agent"],
    viewport_width=viewport.get("width", 1366),
    viewport_height=viewport.get("height", 768),
    timezone=p.get("timezone", "Europe/Moscow"),
    language=p.get("language", "ru-RU"),
    platform=p.get("platform", "Win32"),
    is_mobile=False,
    canvas_fingerprint=p.get("canvas_fingerprint", ""),
    webgl_fingerprint=_json.dumps(p.get("webgl_fingerprint", {})),
    audio_fingerprint=p.get("audio_fingerprint", ""),
    screen_fingerprint=screen_fp,
)
db.add(profile_obj)
db.commit()
print(f"  Saved to DB with id={profile_obj.id}")

# Now build profile_data exactly as yandex_search.py does
profile_data = p.copy()

# Extract screen_fingerprint fields (same as the fixed yandex_search.py)
for k in ('screen', 'css_media', 'feature_flags', 'audio_properties', 'speech_voices', 'sensor',
          'connection_info', 'storage_quota', 'heap_size', 'system_colors',
          'system_fonts', 'codecs', 'keyboard_layout', 'fonts'):
    if k in screen_fp:
        profile_data[k] = screen_fp[k]

# Create browser session
bm = BrowserManager()
bid = None
try:
    bid = bm.create_browser_session(profile_data, None)
    d = bm.active_browsers[bid]

    print("\nOpening browserscan.net...")
    d.get("https://www.browserscan.net/ru")
    
    # Wait for page to load
    time.sleep(5)
    
    # Close cookie consent banner if present
    for attempt in range(3):
        try:
            closed = d.execute_script("""
                // Try various cookie consent selectors
                var selectors = [
                    'button.fc-cta-consent', // common consent button
                    'button[aria-label="Соглашаюсь"]',
                    'button[aria-label="Consent"]',
                    '.fc-consent-root .fc-cta-consent',
                ];
                for (var i = 0; i < selectors.length; i++) {
                    var btn = document.querySelector(selectors[i]);
                    if (btn) { btn.click(); return 'clicked: ' + selectors[i]; }
                }
                // Try by text content
                var buttons = document.querySelectorAll('button');
                for (var j = 0; j < buttons.length; j++) {
                    var txt = buttons[j].textContent.trim();
                    if (txt === 'Соглашаюсь' || txt === 'Accept' || txt === 'Accept all' || txt === 'Consent') {
                        buttons[j].click();
                        return 'clicked by text: ' + txt;
                    }
                }
                // Try close button on overlay
                var close = document.querySelector('.fc-close, [aria-label="Close"]');
                if (close) { close.click(); return 'closed overlay'; }
                return 'no banner found';
            """)
            print(f"  Cookie banner: {closed}")
            if 'clicked' in str(closed) or 'closed' in str(closed):
                time.sleep(2)
                break
        except:
            pass
        time.sleep(2)
    
    # Wait for scan to complete
    for i in range(30):
        time.sleep(2)
        try:
            score = d.execute_script("""
                var el = document.querySelector('.score-item .score-text, .fingerprint-score, [class*=score]');
                return el ? el.textContent : null;
            """)
            if score and len(score.strip()) > 0:
                print(f"  Page loaded, score visible: {score.strip()[:50]}")
                break
        except:
            pass
        if i % 5 == 4:
            print(f"  Waiting... ({(i+1)*2}s)")
    
    # Extra wait for all async checks
    time.sleep(5)
    
    # Take screenshots
    os.makedirs("/app/screenshots", exist_ok=True)
    
    page = d._page
    
    # Full page height
    total_h = d.execute_script("return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
    viewport_h = d.execute_script("return window.innerHeight")
    
    print(f"\nPage height: {total_h}px, viewport: {viewport_h}px")
    
    # Screenshot top
    page.screenshot(path="/app/screenshots/bs_new_top.png", clip={"x": 0, "y": 0, "width": 1400, "height": 900})
    
    # Scroll down and capture sections
    d.execute_script("window.scrollTo(0, 800)")
    time.sleep(1)
    page.screenshot(path="/app/screenshots/bs_new_mid1.png", clip={"x": 0, "y": 0, "width": 1400, "height": 900})
    
    d.execute_script("window.scrollTo(0, 1600)")
    time.sleep(1)
    page.screenshot(path="/app/screenshots/bs_new_mid2.png", clip={"x": 0, "y": 0, "width": 1400, "height": 900})
    
    d.execute_script("window.scrollTo(0, 2400)")
    time.sleep(1)
    page.screenshot(path="/app/screenshots/bs_new_mid3.png", clip={"x": 0, "y": 0, "width": 1400, "height": 900})
    
    d.execute_script("window.scrollTo(0, 3200)")
    time.sleep(1)
    page.screenshot(path="/app/screenshots/bs_new_mid4.png", clip={"x": 0, "y": 0, "width": 1400, "height": 900})
    
    d.execute_script(f"window.scrollTo(0, {total_h})")
    time.sleep(1)
    page.screenshot(path="/app/screenshots/bs_new_bot.png", clip={"x": 0, "y": 0, "width": 1400, "height": 900})
    
    print("Screenshots saved!")
    
    # Extract key data from page
    result = d.execute_script("""
        var getText = function(sel) {
            var el = document.querySelector(sel);
            return el ? el.textContent.trim() : 'N/A';
        };
        var getAllText = function(sel) {
            var els = document.querySelectorAll(sel);
            var texts = [];
            for (var i = 0; i < els.length; i++) texts.push(els[i].textContent.trim());
            return texts;
        };
        return {
            title: document.title,
            bodyText: document.body ? document.body.innerText.substring(0, 3000) : 'no body'
        };
    """)
    
    print(f"\nPage title: {result.get('title', 'N/A')}")
    body = result.get('bodyText', '')
    # Extract key lines
    for line in body.split('\n'):
        line = line.strip()
        if any(kw in line.lower() for kw in ['подлинность', 'робот', 'webgl', 'canvas', 'screen', 'webrtc', 'ip', 'dns', 'timezone', 'connection', 'device', 'memory']):
            if len(line) < 120:
                print(f"  {line}")

finally:
    if bid:
        try:
            bm.close_browser(bid)
        except:
            pass
    # Clean up test profile
    try:
        old = db.query(BrowserProfile).filter(BrowserProfile.name == profile_name).first()
        if old:
            db.delete(old)
            db.commit()
    except:
        pass
    db.close()
    print("\nDone!")
