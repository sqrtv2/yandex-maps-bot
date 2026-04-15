import psutil

# Check what cleanup sees
live_worker_pids = set()
for proc in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        cmdline = " ".join(proc.info.get("cmdline") or [])
        if "celery" in cmdline.lower() and "ForkPoolWorker" in (proc.name() or ""):
            live_worker_pids.add(proc.info["pid"])
    except:
        continue

print(f"Live workers by name: {len(live_worker_pids)}")

# Try tree approach
if not live_worker_pids:
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "celery" in cmdline.lower():
                p = psutil.Process(proc.info["pid"])
                parent = p.parent()
                if parent and "celery" in " ".join(parent.cmdline()).lower():
                    live_worker_pids.add(proc.info["pid"])
        except:
            continue
    print(f"Live workers by tree: {len(live_worker_pids)}")

print(f"Worker PIDs: {live_worker_pids}")

# Count Chrome
chrome_count = 0
orphan_chrome = 0
for proc in psutil.process_iter(["pid", "name", "ppid"]):
    try:
        name = (proc.info.get("name") or "").lower()
        if "chrome" in name:
            chrome_count += 1
            if proc.info.get("ppid", 0) <= 1:
                orphan_chrome += 1
    except:
        continue
print(f"Chrome total: {chrome_count}, orphan (ppid<=1): {orphan_chrome}")

# Check node-drivers
node_drivers = 0
orphan_nodes = 0
for proc in psutil.process_iter(["pid", "cmdline", "ppid"]):
    try:
        cmdline = " ".join(proc.info.get("cmdline") or [])
        if "run-driver" in cmdline:
            node_drivers += 1
            ppid = proc.info.get("ppid", 0)
            if ppid not in live_worker_pids:
                orphan_nodes += 1
                print(f"  Orphan node driver: pid={proc.info['pid']}, ppid={ppid}")
    except:
        continue
print(f"Node-drivers: {node_drivers}, orphan: {orphan_nodes}")
