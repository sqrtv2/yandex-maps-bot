#!/usr/bin/env python3
"""Check Celery workers state."""
from tasks.celery_app import celery_app

insp = celery_app.control.inspect(timeout=10)

# Active tasks
active = insp.active()
if active:
    for worker, tasks in active.items():
        print(f"WORKER: {worker} ({len(tasks)} active)")
        for t in tasks:
            started = t.get("time_start", 0)
            print(f"  {t['name']} runtime_start={started}")
else:
    print("NO ACTIVE WORKERS")

print()

# Reserved
reserved = insp.reserved()
if reserved:
    for worker, tasks in reserved.items():
        print(f"RESERVED {worker}: {len(tasks)} tasks")
else:
    print("NO RESERVED TASKS")

print()

# Stats
stats = insp.stats()
if stats:
    for worker, s in stats.items():
        pool = s.get("pool", {})
        procs = pool.get("processes", [])
        prefetch = s.get("prefetch_count", 0)
        print(f"STATS {worker}: processes={procs} prefetch={prefetch}")
else:
    print("NO STATS")

# Check containers
import subprocess
print("\n=== DOCKER CONTAINERS ===")
