#!/usr/bin/env python3
"""Create browser profiles using the new fingerprint generation algorithm.

Usage:
    python create_100_profiles.py              # 100 desktop profiles
    python create_100_profiles.py 200          # 200 desktop profiles
    python create_100_profiles.py 50 --mobile  # 50 mobile profiles
"""
import sys
import os
import json
import argparse

sys.path.insert(0, os.path.dirname(__file__))

from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from core.profile_generator import ProfileGenerator
from sqlalchemy import func


def main():
    parser = argparse.ArgumentParser(description="Create browser profiles")
    parser.add_argument("count", type=int, nargs="?", default=100, help="Number of profiles to create")
    parser.add_argument("--mobile", action="store_true", help="Create mobile profiles")
    parser.add_argument("--no-warmup", action="store_true", help="Do not start warmup")
    args = parser.parse_args()

    pg = ProfileGenerator()
    count = args.count
    is_mobile = args.mobile

    print(f"Generating {count} {'mobile' if is_mobile else 'desktop'} profiles...")

    with get_db_session() as db:
        max_id = db.query(func.max(BrowserProfile.id)).scalar() or 0
        print(f"Current max profile ID: {max_id}")

        rows = []
        for i in range(1, count + 1):
            profile_name = f"Profile-{max_id + i}"
            p = pg.generate_profile(profile_name, is_mobile=is_mobile)

            viewport = p.get("viewport", {})
            screen = p.get("screen", {})

            # Build screen_fingerprint JSON (screen + css_media + sensor + features + audio props + new vectors)
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
            }
            if p.get("sensor"):
                screen_fp["sensor"] = p["sensor"]

            rows.append(BrowserProfile(
                name=profile_name,
                user_agent=p["user_agent"],
                viewport_width=viewport.get("width", 1366),
                viewport_height=viewport.get("height", 768),
                timezone=p.get("timezone", "Europe/Moscow"),
                language=p.get("language", "ru-RU"),
                platform=p.get("platform", "Win32"),
                is_mobile=is_mobile,
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

            if i % 50 == 0:
                print(f"  Generated {i}/{count}...")

        db.add_all(rows)
        db.commit()

        # Verify
        new_profiles = db.query(BrowserProfile).filter(
            BrowserProfile.id > max_id
        ).order_by(BrowserProfile.id).all()

        profile_ids = [p.id for p in new_profiles]
        print(f"\n✅ Created {len(profile_ids)} profiles: IDs {profile_ids[0]} - {profile_ids[-1]}")

        # Show a sample
        sample = new_profiles[0]
        print(f"\nSample profile: {sample.name}")
        print(f"  UA: {sample.user_agent[:80]}...")
        print(f"  Viewport: {sample.viewport_width}x{sample.viewport_height}")
        print(f"  Platform: {sample.platform}")
        print(f"  Mobile: {sample.is_mobile}")
        webgl = json.loads(sample.webgl_fingerprint) if sample.webgl_fingerprint else {}
        print(f"  WebGL GPU: {webgl.get('unmaskedRenderer', 'N/A')[:60]}")
        print(f"  WebGL keys: {len(webgl)}")

        # Start warmup if requested
        if not args.no_warmup:
            try:
                from tasks.warmup import warmup_profile_task
                print(f"\nStarting warmup for {len(profile_ids)} profiles...")

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
                print(f"Warmup start failed (can be triggered manually later): {e}")
        else:
            print("\nSkipping warmup (--no-warmup flag)")


if __name__ == "__main__":
    main()
