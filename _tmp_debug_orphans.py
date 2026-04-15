#!/usr/bin/env python3
"""Debug: why cleanup_orphaned_chrome misses ppid<=1 Chrome"""
import psutil

# Check Chrome with ppid<=1
for p in psutil.process_iter(["pid", "name", "ppid", "cmdline"]):
    try:
        name = (p.info.get("name") or "").lower()
        if "chrome" not in name:
            continue
        ppid = p.info.get("ppid", 0)
        if ppid <= 1:
            cmd = " ".join(p.info.get("cmdline") or [])[:120]
            print("ORPHAN: pid=%d ppid=%d name=%s cmd=%s" % (p.pid, ppid, p.info["name"], cmd))
            # Check if we can kill it
            try:
                proc = psutil.Process(p.pid)
                print("  status=%s, create_time=%.0f" % (proc.status(), proc.create_time()))
            except Exception as e:
                print("  cant inspect: %s" % e)
    except Exception:
        pass

# Also check: does cleanup actually reach the ppid<=1 section?
print("\n--- Simulating cleanup ---")
live_worker_pids = set()
for proc in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        cmdline = " ".join(proc.info.get("cmdline") or [])
        if "celery" in cmdline.lower() and "ForkPoolWorker" in (proc.name() or ""):
            live_worker_pids.add(proc.info["pid"])
    except Exception:
        continue
if not live_worker_pids:
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "celery" in cmdline.lower():
                p = psutil.Process(proc.info["pid"])
                parent = p.parent()
                if parent and "celery" in " ".join(parent.cmdline()).lower():
                    live_worker_pids.add(proc.info["pid"])
        except Exception:
            continue

print("Live workers: %s" % live_worker_pids)

# Check node-drivers
orphan_nodes = set()
for proc in psutil.process_iter(["pid", "cmdline", "ppid"]):
    try:
        cmdline = " ".join(proc.info.get("cmdline") or [])
        if "run-driver" not in cmdline:
            continue
        ppid = proc.info.get("ppid", 0)
        in_workers = ppid in live_worker_pids
        print("node-driver pid=%d ppid=%d in_workers=%s" % (proc.info["pid"], ppid, in_workers))
        if not in_workers:
            orphan_nodes.add(proc.info["pid"])
    except Exception:
        continue
print("Orphan node-drivers: %s" % orphan_nodes)
