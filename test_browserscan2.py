#!/usr/bin/env python3
"""
Test browserscan.net - wait longer and scroll to capture all results.
"""
import sys, os, time, json, random
sys.path.insert(0, '/app')

from core.browser_manager import BrowserManager
from app.database import get_db_session
from app.models import BrowserProfile, ProxyServer

def main():
    with get_db_session() as db:
        profile = db.query(BrowserProfile).filter(BrowserProfile.is_active == True).first()
        proxy = db.query(ProxyServer).filter(ProxyServer.is_active == True).first()
        
        if not profile:
            print("ERROR: No active profiles")
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
                "id": proxy.id, "host": proxy.host, "port": proxy.port,
                "proxy_type": proxy.proxy_type or "http",
                "username": proxy.username, "password": proxy.password,
            }
            print(f"Proxy: {proxy.host}:{proxy.port}")
        print(f"Profile: {profile.name}")
    
    bm = BrowserManager()
    browser_id = None
    
    try:
        browser_id = bm.create_browser_session(profile_data, proxy_data)
        driver = bm.active_browsers[browser_id]
        
        print("Opening browserscan.net...")
        driver.get("https://www.browserscan.net/ru")
        
        # Wait for page to fully load and all checks to complete
        print("Waiting 30s for all checks to complete...")
        time.sleep(30)
        
        # Screenshot 1: top of page
        driver.save_screenshot("/app/screenshots/browserscan_top.png")
        print("Saved: browserscan_top.png")
        
        # Scroll down to see more results
        driver.execute_script("window.scrollTo(0, 800)")
        time.sleep(2)
        driver.save_screenshot("/app/screenshots/browserscan_mid1.png")
        print("Saved: browserscan_mid1.png")
        
        driver.execute_script("window.scrollTo(0, 1600)")
        time.sleep(2)
        driver.save_screenshot("/app/screenshots/browserscan_mid2.png")
        print("Saved: browserscan_mid2.png")
        
        driver.execute_script("window.scrollTo(0, 2400)")
        time.sleep(2)
        driver.save_screenshot("/app/screenshots/browserscan_mid3.png")
        print("Saved: browserscan_mid3.png")
        
        driver.execute_script("window.scrollTo(0, 3200)")
        time.sleep(2)
        driver.save_screenshot("/app/screenshots/browserscan_bot.png")
        print("Saved: browserscan_bot.png")

        # Full page text
        print("\n=== FULL PAGE TEXT ===")
        text = driver.execute_script("return document.body.innerText.substring(0, 10000)")
        print(text)
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if bm and browser_id:
            try:
                bm.close_browser_session(browser_id)
            except:
                pass

if __name__ == '__main__':
    main()
