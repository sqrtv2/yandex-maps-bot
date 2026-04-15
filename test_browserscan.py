#!/usr/bin/env python3
"""
Open browserscan.net in the bot's browser and capture detection results.
Run inside the celery_yandex_search container.
"""
import sys
import os
import time
import json
import random

sys.path.insert(0, '/app')

from core.browser_manager import BrowserManager
from app.database import get_db_session
from app.models import BrowserProfile, ProxyServer

def main():
    # Pick a random active profile and proxy
    with get_db_session() as db:
        profile = db.query(BrowserProfile).filter(BrowserProfile.is_active == True).first()
        proxy = db.query(ProxyServer).filter(ProxyServer.is_active == True).first()
        
        if not profile:
            print("ERROR: No active profiles found")
            return
        
        profile_data = {
            "name": profile.name,
            "user_agent": profile.user_agent,
            "viewport": {"width": profile.viewport_width or 1366, "height": profile.viewport_height or 768},
            "timezone": profile.timezone or "Europe/Moscow",
            "webgl_fingerprint": json.loads(profile.webgl_fingerprint) if profile.webgl_fingerprint else {},
            "is_mobile": profile.is_mobile if hasattr(profile, 'is_mobile') else False,
            "screen": json.loads(profile.screen) if hasattr(profile, 'screen') and profile.screen else {},
            "navigator": json.loads(profile.navigator) if hasattr(profile, 'navigator') and profile.navigator else {},
            "canvas_fingerprint": profile.canvas_fingerprint if hasattr(profile, 'canvas_fingerprint') else None,
            "audio_fingerprint": profile.audio_fingerprint if hasattr(profile, 'audio_fingerprint') else None,
            "fonts": json.loads(profile.fonts) if hasattr(profile, 'fonts') and profile.fonts else [],
        }
        
        proxy_data = None
        if proxy:
            proxy_data = {
                "id": proxy.id,
                "host": proxy.host,
                "port": proxy.port,
                "proxy_type": proxy.proxy_type or "http",
                "username": proxy.username,
                "password": proxy.password,
            }
            print(f"Using proxy: {proxy.host}:{proxy.port}")
        
        print(f"Using profile: {profile.name}")
        print(f"User-Agent: {profile_data['user_agent'][:80]}...")
    
    bm = BrowserManager()
    browser_id = None
    
    try:
        browser_id = bm.create_browser_session(profile_data, proxy_data)
        driver = bm.active_browsers[browser_id]
        
        print("\n=== Opening browserscan.net ===")
        driver.get("https://www.browserscan.net/ru")
        time.sleep(15)  # Let the page fully analyze
        
        # Take screenshot
        ss_path = "/app/screenshots/browserscan_test.png"
        try:
            driver.save_screenshot(ss_path)
            print(f"Screenshot saved: {ss_path}")
        except Exception as e:
            print(f"Screenshot failed: {e}")
        
        # Get page title and URL
        print(f"\nURL: {driver.current_url}")
        print(f"Title: {driver.title}")
        
        # Extract detection results via JavaScript
        print("\n=== Extracting detection results ===")
        
        # Wait a bit more for all async checks to complete
        time.sleep(10)
        
        # Try to extract the main detection results from the page
        try:
            # Get the full page text content for analysis
            page_text = driver.execute_script("""
                // Try to get structured results
                let results = {};
                
                // Get all text content
                results.bodyText = document.body ? document.body.innerText.substring(0, 8000) : '';
                
                // Try to grab specific detection elements
                let cards = document.querySelectorAll('[class*="card"], [class*="result"], [class*="item"], [class*="detect"]');
                results.cards = [];
                cards.forEach(c => {
                    let text = c.innerText.trim();
                    if (text.length > 5 && text.length < 500) {
                        results.cards.push(text);
                    }
                });
                
                // Check for specific leak indicators
                results.webrtcIP = '';
                try {
                    let rtcEl = document.querySelector('[class*="webrtc"], [class*="ip"]');
                    if (rtcEl) results.webrtcIP = rtcEl.innerText.substring(0, 200);
                } catch(e) {}
                
                return JSON.stringify(results);
            """)
            
            data = json.loads(page_text)
            
            print("\n--- PAGE CONTENT (first 5000 chars) ---")
            body = data.get('bodyText', '')
            # Print in chunks of 200 chars per line for readability
            for i in range(0, min(len(body), 5000), 200):
                print(body[i:i+200])
            
            if data.get('cards'):
                print("\n--- DETECTED CARDS ---")
                for i, card in enumerate(data['cards'][:20]):
                    print(f"  [{i}] {card[:200]}")
                    
        except Exception as e:
            print(f"JS extraction failed: {e}")
            # Fallback: get page source
            try:
                src = driver.page_source[:5000]
                print("\n--- PAGE SOURCE (first 5000) ---")
                print(src)
            except Exception as e2:
                print(f"Page source also failed: {e2}")
        
        # Save full HTML for analysis
        html_path = "/app/screenshots/browserscan_test.html"
        try:
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(driver.page_source or '')
            print(f"\nFull HTML saved: {html_path}")
        except Exception as e:
            print(f"HTML save failed: {e}")
        
        # Additional specific checks
        print("\n=== Browser API Checks ===")
        try:
            checks = driver.execute_script("""
                let r = {};
                r.webdriver = navigator.webdriver;
                r.userAgent = navigator.userAgent;
                r.platform = navigator.platform;
                r.hardwareConcurrency = navigator.hardwareConcurrency;
                r.languages = navigator.languages;
                r.plugins_count = navigator.plugins ? navigator.plugins.length : 0;
                r.chrome_runtime = !!(window.chrome && window.chrome.runtime);
                r.chrome_app = !!(window.chrome && window.chrome.app);
                try { r.webgl_vendor = document.createElement('canvas').getContext('webgl').getParameter(document.createElement('canvas').getContext('webgl').UNMASKED_VENDOR_WEBGL); } catch(e) { r.webgl_vendor = 'N/A'; }
                try { r.webgl_renderer = document.createElement('canvas').getContext('webgl').getParameter(document.createElement('canvas').getContext('webgl').UNMASKED_RENDERER_WEBGL); } catch(e) { r.webgl_renderer = 'N/A'; }
                r.screen_width = screen.width;
                r.screen_height = screen.height;
                r.devicePixelRatio = window.devicePixelRatio;
                r.cookieEnabled = navigator.cookieEnabled;
                r.doNotTrack = navigator.doNotTrack;
                try { r.connection = navigator.connection ? navigator.connection.effectiveType : 'N/A'; } catch(e) { r.connection = 'N/A'; }
                return JSON.stringify(r, null, 2);
            """)
            print(checks)
        except Exception as e:
            print(f"API checks failed: {e}")
            
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if bm and browser_id:
            try:
                bm.close_browser_session(browser_id)
            except Exception:
                pass

if __name__ == '__main__':
    main()
