#!/usr/bin/env python3
"""
Direct browser test - shows Yandex protection visually for 120 seconds
"""
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile
from app.models.yandex_target import YandexMapTarget
from core.browser_manager import BrowserManager
from app.config import settings

def main():
    print("=" * 80)
    print("🔍 DIRECT BROWSER TEST - SHOWING YANDEX PROTECTION")
    print("=" * 80)
    
    db = SessionLocal()
    try:
        # Get Profile-1 with proxy
        profile = db.query(BrowserProfile).filter(
            BrowserProfile.name == "Profile-1"
        ).first()
        
        if not profile:
            print("❌ Profile-1 not found!")
            return
        
        print(f"\n✅ Found profile: {profile.name}")
        print(f"   Proxy: {profile.proxy_type}://{profile.proxy_host}:{profile.proxy_port}")
        print(f"   Auth: {profile.proxy_username}")
        
        # Get Медсемья target
        target = db.query(YandexMapTarget).filter(
            YandexMapTarget.organization_name == "Медсемья"
        ).first()
        
        if not target:
            print("❌ Медсемья target not found!")
            return
        
        print(f"\n✅ Found target: {target.organization_name}")
        print(f"   URL: {target.url}")
        
        # Create browser
        print("\n🔧 Creating browser manager...")
        browser_manager = BrowserManager(
            headless=False,  # FORCE VISIBLE BROWSER
            download_dir=str(project_root / "downloads"),
            screenshots_dir=str(project_root / "screenshots")
        )
        
        # Create session with proxy
        print("\n🌐 Creating browser session WITH PROXY...")
        driver = browser_manager.create_browser_session(
            profile_path=profile.profile_path,
            proxy_host=profile.proxy_host,
            proxy_port=profile.proxy_port,
            proxy_username=profile.proxy_username,
            proxy_password=profile.proxy_password,
            proxy_type=profile.proxy_type
        )
        
        if not driver:
            print("❌ Failed to create driver!")
            return
        
        print("\n✅ Browser created successfully!")
        
        # Navigate to target
        print(f"\n🔍 Navigating to: {target.url}")
        driver.get(target.url)
        
        print("\n" + "=" * 80)
        print("🖥️  BROWSER IS NOW VISIBLE ON YOUR SCREEN")
        print("=" * 80)
        print("⏰ Will stay open for 120 seconds...")
        print("👀 Look at the browser window - see what Yandex shows!")
        print("=" * 80)
        
        # Keep browser open
        for i in range(120, 0, -10):
            print(f"⏳ Remaining: {i} seconds...")
            time.sleep(10)
        
        print("\n📸 Taking final screenshot...")
        screenshot_path = project_root / "screenshots" / "yandex_protection_visible.png"
        driver.save_screenshot(str(screenshot_path))
        print(f"✅ Screenshot saved: {screenshot_path}")
        
        # Get page source
        print("\n📄 Checking page title...")
        print(f"Title: {driver.title}")
        
        # Check for protection elements
        page_source = driver.page_source.lower()
        protection_keywords = [
            'captcha', 'robot', 'подтвердите', 'защита', 
            'smartcaptcha', 'проверка', 'security'
        ]
        
        print("\n🔍 Checking for protection elements:")
        for keyword in protection_keywords:
            if keyword in page_source:
                print(f"   ✓ Found: {keyword}")
            else:
                print(f"   ✗ Not found: {keyword}")
        
        print("\n✅ Test complete! Check the screenshot.")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            if 'driver' in locals():
                print("\n🔒 Closing browser...")
                driver.quit()
        except:
            pass
        db.close()

if __name__ == "__main__":
    main()
