#!/usr/bin/env python3
"""Check for orphaned node-drivers and Chrome"""
import psutil

# Count node-drivers and their Chrome children
node_drivers = []
for p in psutil.process_iter(["pid", "cmdline"]):
    try:
        cmd = " ".join(p.info.get("cmdline") or [])
        if "run-driver" in cmd:
            children = psutil.Process(p.pid).children(recursive=True)
            chrome_kids = [c for c in children if "chrome" in (c.name() or "").lower()]
            node_drivers.append({"pid": p.pid, "ppid": p.ppid(), "chrome_count": len(chrome_kids)})
    except Exception:
        pass

print("Node-drivers: %d" % len(node_drivers))
for nd in node_drivers:
    ppid = nd["ppid"]
    try:
        parent = psutil.Process(ppid)
        parent_name = parent.name()
        parent_cmd = " ".join(parent.cmdline())[:80]
    except Exception:
        parent_name = "dead"
        parent_cmd = ""
    is_worker = "ForkPoolWorker" in parent_name or "celery" in parent_cmd.lower()
    status = "ACTIVE" if is_worker else "ORPHAN"
    print("  [%s] PID=%d, ppid=%d (%s), chrome=%d" % (status, nd["pid"], ppid, parent_name, nd["chrome_count"]))

# Also check for Chrome with ppid=1
orphan_chrome = 0
for p in psutil.process_iter(["pid", "name", "ppid"]):
    try:
        if "chrome" in (p.info.get("name") or "").lower() and p.info.get("ppid", 0) <= 1:
            orphan_chrome += 1
    except Exception:
        pass
print("\nChrome with ppid<=1 (directly orphaned): %d" % orphan_chrome)
