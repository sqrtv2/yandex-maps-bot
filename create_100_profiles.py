#!/usr/bin/env python3
"""Create 100 new profiles with Russian locale and start warmup."""
import sys
import os
import random

sys.path.insert(0, os.path.dirname(__file__))

from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from sqlalchemy import func

def main():
    viewports = [
        (1366, 768), (1920, 1080), (1440, 900), (1536, 864),
        (1280, 720), (1600, 1200), (2560, 1440), (1024, 768)
    ]
    timezones = [
        "Europe/Moscow", "Europe/Moscow", "Europe/Moscow",
        "Europe/Samara", "Asia/Yekaterinburg", "Europe/Volgograd",
    ]
    languages = ["ru-RU"]
    platforms = ["Win32", "MacIntel", "Linux x86_64"]
    
    ua_pool = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ]
    
    with get_db_session() as db:
        max_id = db.query(func.max(BrowserProfile.id)).scalar() or 0
        print(f"Current max profile ID: {max_id}")
        
        rows = []
        for i in range(1, 101):
            w, h = random.choice(viewports)
            profile_name = f"Profile-{max_id + i}"
            rows.append(BrowserProfile(
                name=profile_name,
                user_agent=random.choice(ua_pool),
                viewport_width=w,
                viewport_height=h,
                timezone=random.choice(timezones),
                language=random.choice(languages),
                platform=random.choice(platforms),
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
        print(f"Created {len(profile_ids)} profiles: IDs {profile_ids[0]} - {profile_ids[-1]}")
        print(f"Names: {new_profiles[0].name} - {new_profiles[-1].name}")
        
        # Now trigger warmup via Celery
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
            print(f"First task ID: {task_ids[0] if task_ids else 'none'}")
            print(f"Last task ID: {task_ids[-1] if task_ids else 'none'}")
        except Exception as e:
            print(f"Error starting warmup: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
