"""
Test browserscan.net with a REAL warmed profile + proxy — same as production search tasks.
Takes screenshots and extracts all scan results.
"""
import sys, os, time, json
sys.path.insert(0, "/app")

from core.browser_manager import BrowserManager
from core.proxy_manager import ProxyManager
from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile
from sqlalchemy import text

db = SessionLocal()

# 1. Pick a real warmed profile
profile = db.query(BrowserProfile).filter(
    BrowserProfile.is_active == True,
    BrowserProfile.warmup_completed == True,
).first()

if not profile:
    profile = db.query(BrowserProfile).filter(
        BrowserProfile.is_active == True,
        BrowserProfile.warmup_stage >= 1,
    ).first()

if not profile:
    profile = db.query(BrowserProfile).filter(
        BrowserProfile.is_active == True,
    ).first()

if not profile:
    print("ERROR: No profiles found!")
    sys.exit(1)

print(f"=== Profile: {profile.name} (id={profile.id}, stage={profile.warmup_stage}) ===")
print(f"  UA: {profile.user_agent[:100]}...")
print(f"  Platform: {profile.platform}")
print(f"  Viewport: {profile.viewport_width}x{profile.viewport_height}")
print(f"  Mobile: {profile.is_mobile}")
print(f"  Timezone: {profile.timezone}")

# 2. Get proxy (same as production)
pm = ProxyManager()
pm.load_proxies_from_db()
proxy_data = None
if pm.active_proxies:
    # Pick first working proxy
    for pid, pinfo in pm.active_proxies.items():
        proxy_data = {
            'host': pinfo['host'],
            'port': pinfo['port'],
            'username': pinfo['username'],
            'password': pinfo['password'],
            'proxy_type': pinfo.get('proxy_type', 'http'),
        }
        print(f"  Proxy: {pinfo['host']}:{pinfo['port']}")
        break
else:
    print("  WARNING: No proxy found!")

# 3. Build profile_data exactly as production tasks do
from core.profile_generator import ProfileGenerator
pg = ProfileGenerator()
profile_data = pg.generate_profile(profile.name, is_mobile=profile.is_mobile)

# Override with stored DB values (consistency)
profile_data['user_agent'] = profile.user_agent
profile_data['viewport'] = {
    'width': profile.viewport_width,
    'height': profile.viewport_height,
}
profile_data['timezone'] = profile.timezone or 'Europe/Moscow'
profile_data['language'] = profile.language or 'ru-RU'
profile_data['platform'] = profile.platform or 'Win32'

# WebGL from DB
if profile.webgl_fingerprint:
    try:
        wgl = json.loads(profile.webgl_fingerprint) if isinstance(profile.webgl_fingerprint, str) else profile.webgl_fingerprint
        if wgl and 'unmaskedVendor' in wgl:
            profile_data['webgl_fingerprint'] = wgl
    except:
        pass

# Screen fingerprint from DB
if profile.screen_fingerprint:
    sf = profile.screen_fingerprint if isinstance(profile.screen_fingerprint, dict) else json.loads(profile.screen_fingerprint)
    for k in ('screen', 'css_media', 'feature_flags', 'audio_properties', 'speech_voices', 'sensor',
              'connection_info', 'storage_quota', 'heap_size', 'system_colors',
              'system_fonts', 'codecs', 'keyboard_layout', 'fonts',
              'hardware_concurrency', 'device_memory', 'max_touch_points', 'do_not_track',
              'webgpu_fingerprint'):
        if k in sf:
            profile_data[k] = sf[k]

# 4. Launch browser with proxy
bm = BrowserManager()
bid = None
try:
    bid = bm.create_browser_session(profile_data, proxy_data)
    d = bm.active_browsers[bid]

    # First check our IP through proxy
    print("\n--- Checking IP ---")
    d.get("https://httpbin.org/ip")
    time.sleep(3)
    try:
        ip_text = d.execute_script("return document.body.innerText")
        print(f"  External IP: {ip_text.strip()}")
    except:
        pass

    print("\n--- Opening browserscan.net ---")
    d.get("https://www.browserscan.net/ru")
    time.sleep(8)

    # Close cookie banner
    for attempt in range(3):
        try:
            closed = d.execute_script("""
                var buttons = document.querySelectorAll('button');
                for (var j = 0; j < buttons.length; j++) {
                    var txt = buttons[j].textContent.trim().toLowerCase();
                    if (txt.indexOf('accept') >= 0 || txt.indexOf('consent') >= 0 || 
                        txt.indexOf('agree') >= 0 || txt.indexOf('соглас') >= 0) {
                        buttons[j].click();
                        return 'clicked: ' + txt;
                    }
                }
                var fc = document.querySelector('.fc-cta-consent, button.fc-cta-consent');
                if (fc) { fc.click(); return 'clicked fc-consent'; }
                return 'no banner';
            """)
            if 'clicked' in str(closed):
                print(f"  Cookie banner: {closed}")
                time.sleep(2)
                break
        except:
            pass
        time.sleep(2)

    # Wait for scan
    print("  Waiting for scan to complete...")
    time.sleep(30)

    # Take FULL PAGE screenshot
    os.makedirs("/app/screenshots", exist_ok=True)
    page = d._page

    # Extract ALL text from page for analysis FIRST (before screenshots that may timeout)
    d.execute_script("window.scrollTo(0, 0)")
    time.sleep(1)
    
    body_text = d.execute_script("return document.body ? document.body.innerText : ''")
    
    print("\n" + "=" * 70)
    print("BROWSERSCAN FULL RESULTS:")
    print("=" * 70)
    
    # Print all meaningful lines
    for line in body_text.split('\n'):
        line = line.strip()
        if line and len(line) > 1 and len(line) < 200:
            # Skip menu/navigation noise
            if any(skip in line.lower() for skip in ['войти', 'sign in', 'pricing', 'copyright', 'cookie', 'privacy', 'регистр']):
                continue
            print(f"  {line}")
    
    print("\n" + "=" * 70)

    # Also extract structured data if possible
    structured = d.execute_script("""
        var results = {};
        // Try to find key-value pairs in the scan results
        var rows = document.querySelectorAll('tr, .info-item, .detail-item, [class*=result], [class*=info]');
        for (var i = 0; i < Math.min(rows.length, 100); i++) {
            var text = rows[i].innerText.trim();
            if (text.length > 0 && text.length < 300) {
                results['row_' + i] = text;
            }
        }
        // Get all spans/divs with data
        var items = document.querySelectorAll('.basic-info-value, .value, [class*=value], [class*=result-text]');
        for (var j = 0; j < Math.min(items.length, 50); j++) {
            var prev = items[j].previousElementSibling;
            var label = prev ? prev.innerText.trim() : 'item_' + j;
            results[label] = items[j].innerText.trim();
        }
        return results;
    """)
    
    if structured:
        print("\nSTRUCTURED DATA:")
        for k, v in structured.items():
            if v and len(v.strip()) > 0:
                print(f"  {k}: {v[:150]}")

finally:
    if bid:
        try:
            bm.close_browser_session(bid)
        except:
            try:
                bm.close_browser(bid)
            except:
                pass
    db.close()
    print("\nDone!")
