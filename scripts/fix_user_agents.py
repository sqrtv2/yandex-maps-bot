"""
Fix all browser profile user agents to match the installed Chrome version (143-145).

Old UAs (Chrome 77-122, Firefox) are instantly detectable because the actual
browser is Chrome 145 and navigator.userAgentData reveals the real version.

This script updates all profiles in the DB to use modern Chrome UAs that match
the actual browser, preserving the OS type where possible.
"""
import random
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile

# Modern Chrome UA templates
WINDOWS_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
]
MAC_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
]
LINUX_UAS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
]

# Chrome version templates (matching what's installed)
CHROME_VERSIONS = [
    "143.0.7544.{patch}",
    "144.0.7612.{patch}",
    "145.0.7632.{patch}",
]

# Platform mapping
PLATFORM_FOR_UA = {
    'windows': 'Win32',
    'mac': 'MacIntel',
    'linux': 'Linux x86_64',
}


def generate_modern_ua(os_type='windows'):
    """Generate a modern Chrome UA for the given OS type."""
    if os_type == 'mac':
        template = random.choice(MAC_UAS)
    elif os_type == 'linux':
        template = random.choice(LINUX_UAS)
    else:
        template = random.choice(WINDOWS_UAS)
    
    version_template = random.choice(CHROME_VERSIONS)
    patch = random.randint(40, 120)
    version = version_template.format(patch=patch)
    return template.format(ver=version)


def detect_os_from_ua(ua: str) -> str:
    """Detect OS type from existing UA string."""
    if not ua:
        return 'windows'
    ua_lower = ua.lower()
    if 'macintosh' in ua_lower or 'mac os' in ua_lower:
        return 'mac'
    elif 'linux' in ua_lower or 'x11' in ua_lower or 'ubuntu' in ua_lower:
        return 'linux'
    return 'windows'


def main():
    db = SessionLocal()
    try:
        profiles = db.query(BrowserProfile).all()
        total = len(profiles)
        updated = 0
        
        for profile in profiles:
            old_ua = profile.user_agent or ''
            
            # Check if UA already uses Chrome 143+
            chrome_match = re.search(r'Chrome/(\d+)', old_ua)
            if chrome_match:
                chrome_ver = int(chrome_match.group(1))
                if chrome_ver >= 143:
                    continue  # Already modern
            
            # Detect OS from old UA to keep consistent
            os_type = detect_os_from_ua(old_ua)
            
            # Generate new UA
            new_ua = generate_modern_ua(os_type)
            profile.user_agent = new_ua
            
            # Fix platform to match UA
            profile.platform = PLATFORM_FOR_UA.get(os_type, 'Win32')
            
            updated += 1
            if updated <= 10:
                print(f"  Profile {profile.id} ({profile.name}): Chrome/{chrome_match.group(1) if chrome_match else '?'} -> {re.search(r'Chrome/([0-9.]+)', new_ua).group(1)}")
        
        if updated > 10:
            print(f"  ... and {updated - 10} more profiles")
        
        db.commit()
        print(f"\n✅ Updated {updated}/{total} profiles with modern Chrome UAs")
        
    finally:
        db.close()


if __name__ == '__main__':
    main()
