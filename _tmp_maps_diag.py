from app.database import SessionLocal
from app.models.task import Task
from sqlalchemy import func
from datetime import datetime, timedelta

db = SessionLocal()

# Check maps targets
try:
    from app.models.yandex_target import YandexTarget
    targets = db.query(YandexTarget).filter(YandexTarget.is_active == True).all()
    print("Active maps targets: %d" % len(targets))
    for t in targets:
        url = t.yandex_maps_url[:60] if t.yandex_maps_url else "N/A"
        print("  #%d %s url=%s" % (t.id, t.name, url))
        print("    visits_per_day=%s today_visits=%s today_successful=%s" % (
            t.visits_per_day, t.today_visits, t.today_successful))
        print("    last_visit=%s" % t.last_visit_at)
except Exception as e:
    print("Error loading YandexTarget: %s" % e)

# Try alternate model name
try:
    from app.models.target import Target
    targets2 = db.query(Target).all()
    print("\nTargets (from target.py): %d" % len(targets2))
    for t in targets2[:5]:
        print("  #%d %s active=%s" % (t.id, getattr(t, 'name', '?'), getattr(t, 'is_active', '?')))
except Exception as e:
    print("No Target model: %s" % e)

# Check maps task types
print("\n--- Task types today ---")
today = datetime.utcnow().replace(hour=0, minute=0, second=0)
types = db.query(Task.task_type, func.count(Task.id)).filter(
    Task.created_at > today
).group_by(Task.task_type).all()
for tt, cnt in types:
    print("  %s: %d" % (tt, cnt))

# Check maps tasks (various type names)
for task_type in ["yandex_visit", "yandex_maps", "visit_yandex_maps", "maps_visit"]:
    cnt = db.query(func.count(Task.id)).filter(
        Task.task_type == task_type,
        Task.created_at > today
    ).scalar()
    if cnt:
        print("\nMaps tasks today (%s): %d" % (task_type, cnt))

# Recent maps tasks
for task_type in ["yandex_visit", "yandex_maps"]:
    recent = db.query(Task).filter(Task.task_type == task_type).order_by(Task.id.desc()).limit(5).all()
    if recent:
        print("\nRecent %s tasks:" % task_type)
        for t in recent:
            err = str(t.error_message)[:100] if t.error_message else "ok"
            dur = ""
            if t.started_at and t.completed_at:
                dur = " dur=%ds" % (t.completed_at - t.started_at).total_seconds()
            print("  #%d %s created=%s%s | %s" % (t.id, t.status, t.created_at, dur, err))

db.close()
