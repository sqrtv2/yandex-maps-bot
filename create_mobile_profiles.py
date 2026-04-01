#!/usr/bin/env python3
"""Create mobile (Android) profiles with full fingerprint data and start warmup."""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))

from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from core.profile_generator import ProfileGenerator
from sqlalchemy import func


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    pg = ProfileGenerator()

    with get_db_session() as db:
        # Add is_mobile column if it doesn't exist
        try:
            from sqlalchemy import text
            db.execute(text("ALTER TABLE browser_profiles ADD COLUMN is_mobile BOOLEAN DEFAULT 0"))
            db.commit()
            print("Added is_mobile column to browser_profiles table")
        except Exception as e:
            db.rollback()
            if 'duplicate' in str(e).lower() or 'already exists' in str(e).lower():
                print("is_mobile column already exists")
            else:
                print(f"Column add result: {e}")

        max_id = db.query(func.max(BrowserProfile.id)).scalar() or 0
        print(f"Current max profile ID: {max_id}")

        rows = []
        for i in range(1, count + 1):
            profile_name = f"Profile-{max_id + i}"
            p = pg.generate_profile(profile_name, is_mobile=True)

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

            rows.append(BrowserProfile(
                name=profile_name,
                user_agent=p["user_agent"],
                viewport_width=viewport.get("width", 412),
                viewport_height=viewport.get("height", 915),
                timezone=p.get("timezone", "Europe/Moscow"),
                language=p.get("language", "ru-RU"),
                platform=p.get("platform", "Linux armv81"),
                is_mobile=True,
                canvas_fingerprint=p.get("canvas_fingerprint", ""),
                webgl_fingerprint=json.dumps(p.get("webgl_fingerprint", {})),
                audio_fingerprint=p.get("audio_fingerprint", ""),
                screen_fingerprint=screen_fp,
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

        new_profiles = db.query(BrowserProfile).filter(
            BrowserProfile.id > max_id
        ).order_by(BrowserProfile.id).all()

        profile_ids = [p.id for p in new_profiles]
        print(f"\nCreated {len(profile_ids)} MOBILE profiles: IDs {profile_ids[0]} - {profile_ids[-1]}")
        for p in new_profiles:
            print(f"  {p.name}: {p.viewport_width}x{p.viewport_height}")

        # Auto-start warmup (non-interactive for docker)
        try:
            from tasks.warmup import warmup_profile_task
            print(f"\nStarting warmup for {len(profile_ids)} profiles...")
            for pid in profile_ids:
                warmup_profile_task.delay(pid)
            print(f"Started {len(profile_ids)} warmup tasks")
        except Exception as e:
            print(f"Error starting warmup: {e}")


if __name__ == "__main__":
    main()
