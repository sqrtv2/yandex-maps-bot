import psutil
for proc in psutil.process_iter(['pid', 'ppid', 'name', 'cmdline']):
    try:
        name = (proc.info.get('name') or '').lower()
        cmdline = ' '.join(proc.info.get('cmdline') or [])
        if 'run-driver' in cmdline or 'chromium' in name or 'chrome' in name:
            print(f"PID={proc.info['pid']} PPID={proc.info['ppid']} NAME={proc.info['name']} CMD={cmdline[:200]}")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        continue
