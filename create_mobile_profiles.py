#!/usr/bin/env python3
"""Create mobile (Android) profiles with Russian locale and start warmup."""
import sys
import os
import random

sys.path.insert(0, os.path.dirname(__file__))

from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from sqlalchemy import func


# Mobile viewports (portrait: width x height)
MOBILE_VIEWPORTS = [
    (360, 800), (375, 812), (390, 844), (393, 873),
    (412, 915), (414, 896), (360, 780), (384, 854),
    (411, 731), (375, 667), (428, 926),
]

# Mobile device configs for UA generation
MOBILE_DEVICES = [
    {"model": "Pixel 7", "android": "14"},
    {"model": "Pixel 8", "android": "14"},
    {"model": "SM-S928B", "android": "14"},   # Samsung Galaxy S24 Ultra
    {"model": "SM-A546B", "android": "14"},   # Samsung Galaxy A54
    {"model": "SM-G998B", "android": "13"},   # Samsung Galaxy S21 Ultra
    {"model": "22101316G", "android": "14"},  # Xiaomi 13
    {"model": "2201117TG", "android": "13"},  # Xiaomi 12
    {"model": "CPH2451", "android": "13"},    # OPPO Reno 8
    {"model": "RMX3630", "android": "14"},    # Realme 11 Pro
]

CHROME_VERSIONS = [
    "143.0.7544.{patch}",
    "144.0.7612.{patch}",
    "145.0.7632.{patch}",
]

TIMEZONES = [
    "Europe/Moscow", "Europe/Moscow", "Europe/Moscow",
    "Europe/Samara", "Asia/Yekaterinburg", "Europe/Volgograd",
]


def generate_mobile_ua(device, chrome_ver):
    return (
        f"Mozilla/5.0 (Linux; Android {device['android']}; {device['model']}) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Mobile Safari/537.36"
    )


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    
    with get_db_session() as db:
        # Add is_mobile column if it doesn't exist
        try:
            from sqlalchemy import text
            db.execute(text("ALTER TABLE browser_profiles ADD COLUMN is_mobile BOOLEAN DEFAULT 0"))
            db.commit()
            print("✅ Added is_mobile column to browser_profiles table")
        except Exception as e:
            db.rollback()
            if 'duplicate' in str(e).lower() or 'already exists' in str(e).lower():
                print("ℹ️  is_mobile column already exists")
            else:
                print(f"ℹ️  Column add result: {e}")
        
        max_id = db.query(func.max(BrowserProfile.id)).scalar() or 0
        print(f"Current max profile ID: {max_id}")
        
        rows = []
        for i in range(1, count + 1):
            w, h = random.choice(MOBILE_VIEWPORTS)
            device = random.choice(MOBILE_DEVICES)
            ver_template = random.choice(CHROME_VERSIONS)
            patch = random.randint(40, 120)
            chrome_ver = ver_template.format(patch=patch)
            ua = generate_mobile_ua(device, chrome_ver)
            
            profile_name = f"Profile-{max_id + i}"
            rows.append(BrowserProfile(
                name=profile_name,
                user_agent=ua,
                viewport_width=w,
                viewport_height=h,
                timezone=random.choice(TIMEZONES),
                language="ru-RU",
                platform="Linux armv81",
                is_mobile=True,
                status="created",
                is_active=True,
                warmup_completed=False,
                warmup_sessions_count=0,
                warmup_time_spent=0,
                total_sessions=0,
                successful_sessions=0,
                failed_sessions=0,
                webrtc_leak_protect=True,
                geolocation_enabled=False,
                notifications_enabled=False,
            ))
        
        db.add_all(rows)
        db.commit()
        
        # Get the IDs of newly created profiles
        new_profiles = db.query(BrowserProfile).filter(
            BrowserProfile.id > max_id
        ).order_by(BrowserProfile.id).all()
        
        profile_ids = [p.id for p in new_profiles]
        print(f"\n📱 Created {len(profile_ids)} MOBILE profiles: IDs {profile_ids[0]} - {profile_ids[-1]}")
        print(f"Names: {new_profiles[0].name} - {new_profiles[-1].name}")
        
        # Show summary
        for p in new_profiles:
            device_model = "unknown"
            for d in MOBILE_DEVICES:
                if d['model'] in p.user_agent:
                    device_model = d['model']
                    break
            print(f"  {p.name}: {device_model} ({p.viewport_width}x{p.viewport_height})")
        
        # Now trigger warmup via Celery
        start_warmup = input(f"\nStart warmup for {len(profile_ids)} profiles? (y/n): ").strip().lower()
        if start_warmup == 'y':
            try:
                from tasks.warmup import warmup_profile_task
                print(f"\nStarting warmup for {len(profile_ids)} profiles...")
                
                # Update status to warming_up
                db.query(BrowserProfile).filter(
                    BrowserProfile.id.in_(profile_ids)
                ).update({"status": "warming_up"}, synchronize_session=False)
                db.commit()
                
                task_ids = []
                for pid in profile_ids:
                    try:
                        r = warmup_profile_task.delay(pid, 30)
                        task_ids.append(r.id)
                    except Exception as e:
                        print(f"  Failed to start warmup for profile {pid}: {e}")
                
                print(f"Started {len(task_ids)} warmup tasks")
            except Exception as e:
                print(f"Error starting warmup: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("Skipping warmup. You can start it later from the web interface.")


if __name__ == "__main__":
    main()
