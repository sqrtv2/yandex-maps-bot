from tasks.celery_app import celery_app

i = celery_app.control.inspect(timeout=5)
active = i.active() or {}
reserved = i.reserved() or {}
scheduled = i.scheduled() or {}

print("=== ACTIVE TASKS ===")
for w, tasks in sorted(active.items()):
    names = {}
    for t in tasks:
        n = t.get("name", "unknown")
        names[n] = names.get(n, 0) + 1
    wname = w.split("@")[0]
    detail = ", ".join(str(v) + " x " + k.split(".")[-1] for k, v in names.items())
    print("  %s: %d active  (%s)" % (wname, len(tasks), detail))

print()
print("=== RESERVED (prefetched on workers) ===")
for w, tasks in sorted(reserved.items()):
    if tasks:
        wname = w.split("@")[0]
        print("  %s: %d" % (wname, len(tasks)))

print()
total_active = sum(len(v) for v in active.values())
total_reserved = sum(len(v) for v in reserved.values())
total_scheduled = sum(len(v) for v in scheduled.values())
print("TOTAL ACTIVE:    %d" % total_active)
print("TOTAL RESERVED:  %d" % total_reserved)
print("TOTAL SCHEDULED: %d" % total_scheduled)
print("GRAND TOTAL:     %d" % (total_active + total_reserved + total_scheduled))
