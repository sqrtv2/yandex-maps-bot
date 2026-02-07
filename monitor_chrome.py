#!/usr/bin/env python3
"""
Script to monitor and cleanup orphaned Chrome processes during warmup.
"""
import subprocess
import time
import signal
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database import SessionLocal
from app.models import BrowserProfile


def get_chrome_processes():
    """Get count of Chrome/ChromeDriver processes."""
    try:
        chrome = subprocess.check_output(
            ["ps", "aux"], 
            stderr=subprocess.DEVNULL
        ).decode('utf-8')
        
        chrome_count = len([
            line for line in chrome.split('\n') 
            if 'Google Chrome.app' in line and 'grep' not in line
        ])
        
        driver_count = len([
            line for line in chrome.split('\n') 
            if 'chromedriver' in line and 'grep' not in line
        ])
        
        return chrome_count, driver_count
    except:
        return 0, 0


def get_active_warmup_count():
    """Get number of profiles currently warming up."""
    try:
        with SessionLocal() as db:
            return db.query(BrowserProfile).filter(
                BrowserProfile.status == 'warming_up'
            ).count()
    except:
        return 0


def cleanup_orphaned_processes(force=False):
    """Kill orphaned Chrome processes."""
    chrome_count, driver_count = get_chrome_processes()
    active_warmups = get_active_warmup_count()
    
    expected_processes = active_warmups * 10  # ~10 processes per profile
    total_processes = chrome_count + driver_count
    
    print(f"\n📊 Статус процессов:")
    print(f"  Chrome: {chrome_count}")
    print(f"  ChromeDriver: {driver_count}")
    print(f"  Всего: {total_processes}")
    print(f"  Активных нагулов: {active_warmups}")
    print(f"  Ожидается процессов: ~{expected_processes}")
    
    # Если процессов значительно больше чем ожидается
    if total_processes > expected_processes + 20 or force:
        print(f"\n⚠️  Обнаружено {total_processes - expected_processes} лишних процессов!")
        print("🔪 Очищаем зависшие процессы...")
        
        # Kill ChromeDriver processes
        subprocess.run(
            ["pkill", "-f", "undetected_chromedriver"],
            stderr=subprocess.DEVNULL
        )
        time.sleep(1)
        
        # Kill Chrome processes from browser_profiles
        try:
            ps_output = subprocess.check_output(
                ["ps", "aux"],
                stderr=subprocess.DEVNULL
            ).decode('utf-8')
            
            for line in ps_output.split('\n'):
                if 'Google Chrome.app' in line and 'browser_profiles' in line:
                    parts = line.split()
                    if len(parts) > 1:
                        pid = parts[1]
                        try:
                            os.kill(int(pid), signal.SIGKILL)
                        except:
                            pass
        except:
            pass
        
        time.sleep(1)
        
        # Check result
        chrome_after, driver_after = get_chrome_processes()
        print(f"✅ Очистка завершена!")
        print(f"  Chrome: {chrome_after} (было {chrome_count})")
        print(f"  ChromeDriver: {driver_after} (было {driver_count})")
        
        return True
    else:
        print("✅ Количество процессов в норме")
        return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Monitor and cleanup Chrome processes')
    parser.add_argument('--monitor', action='store_true', help='Continuous monitoring mode')
    parser.add_argument('--force', action='store_true', help='Force cleanup all processes')
    parser.add_argument('--interval', type=int, default=60, help='Monitoring interval in seconds')
    
    args = parser.parse_args()
    
    if args.monitor:
        print("🔍 Запуск мониторинга процессов Chrome...")
        print(f"⏱️  Интервал проверки: {args.interval} секунд")
        print("Press Ctrl+C to stop\n")
        
        try:
            while True:
                cleanup_orphaned_processes(force=args.force)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n\n👋 Мониторинг остановлен")
    else:
        cleanup_orphaned_processes(force=args.force)
