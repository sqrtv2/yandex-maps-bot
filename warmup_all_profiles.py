#!/usr/bin/env python3
"""
Script to start warmup for all non-warmed profiles.
"""
import sys
import os
import requests
import time
from app.database import get_db_session
from app.models import BrowserProfile

def main():
    """Start warmup for all profiles that need it."""
    
    # Check if API is running
    try:
        response = requests.get("http://127.0.0.1:8000/health", timeout=5)
        if response.status_code != 200:
            print("❌ API is not responding. Please start the FastAPI server first:")
            print("   python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload")
            sys.exit(1)
    except requests.exceptions.RequestException:
        print("❌ Cannot connect to API at http://127.0.0.1:8000")
        print("   Please start the FastAPI server first:")
        print("   python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload")
        sys.exit(1)
    
    print("✅ API is running\n")
    
    # Get profiles that need warmup
    with get_db_session() as db:
        profiles = db.query(BrowserProfile).filter(
            BrowserProfile.warmup_completed == False,
            BrowserProfile.is_active == True,
            BrowserProfile.status.in_(['created', 'error'])
        ).all()
        
        if not profiles:
            print("ℹ️  No profiles need warmup")
            
            # Show current profile statuses
            all_profiles = db.query(BrowserProfile).all()
            print(f"\n📊 Profile Status Summary:")
            print(f"{'ID':<5} {'Name':<15} {'Status':<15} {'Warmed':<8}")
            print("-" * 50)
            for p in all_profiles:
                warmed = "✅" if p.warmup_completed else "❌"
                print(f"{p.id:<5} {p.name:<15} {p.status:<15} {warmed:<8}")
            
            return
        
        print(f"🔍 Found {len(profiles)} profiles that need warmup:\n")
        for profile in profiles:
            print(f"  - {profile.name} (ID: {profile.id})")
        
        print(f"\n🚀 Starting warmup for {len(profiles)} profiles...\n")
        
        # Start warmup for each profile
        started = 0
        failed = 0
        
        for profile in profiles:
            try:
                print(f"Starting warmup for {profile.name}...", end=" ")
                response = requests.post(
                    f"http://127.0.0.1:8000/api/profiles/{profile.id}/start-warmup",
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                
                if response.status_code == 200:
                    result = response.json()
                    task_id = result.get("task_id", "N/A")
                    print(f"✅ Started (Task: {task_id})")
                    started += 1
                    time.sleep(1)  # Small delay between starts
                else:
                    error = response.json().get("detail", "Unknown error")
                    print(f"❌ Failed: {error}")
                    failed += 1
                    
            except Exception as e:
                print(f"❌ Error: {e}")
                failed += 1
        
        print(f"\n📊 Summary:")
        print(f"  ✅ Successfully started: {started}")
        print(f"  ❌ Failed: {failed}")
        print(f"\n💡 Tip: Monitor progress at http://127.0.0.1:8000/profiles")
        print(f"   Or check logs: tail -f logs/celery.log")


if __name__ == "__main__":
    main()
