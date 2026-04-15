import psutil
print("All processes:")
for proc in psutil.process_iter(['pid', 'ppid', 'name', 'cmdline']):
    try:
        name = (proc.info.get('name') or '').lower()
        cmdline = ' '.join(proc.info.get('cmdline') or [])
        if any(x in cmdline.lower() for x in ['run-driver', 'chromium', 'chrome', 'playwright', 'node']):
            print(f"PID={proc.info['pid']} PPID={proc.info['ppid']} NAME={proc.info['name']} CMD={cmdline[:300]}")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        continue

print("\n--- All non-celery processes ---")
for proc in psutil.process_iter(['pid', 'ppid', 'name']):
    try:
        name = (proc.info.get('name') or '')
        if name not in ('celery', 'python3', 'python', 'ps', 'bash', 'sh'):
            print(f"PID={proc.info['pid']} PPID={proc.info['ppid']} NAME={name}")
    except:
        continue
