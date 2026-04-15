from app.database import SessionLocal
from app.models.task import Task
from sqlalchemy import func
from datetime import datetime

db = SessionLocal()
today = datetime.utcnow().replace(hour=0, minute=0, second=0)

# Maps task stats today
comp = db.query(func.count(Task.id)).filter(
    Task.task_type == "yandex_visit", Task.status == "completed", Task.created_at > today
).scalar()
fail = db.query(func.count(Task.id)).filter(
    Task.task_type == "yandex_visit", Task.status == "failed", Task.created_at > today
).scalar()
pend = db.query(func.count(Task.id)).filter(
    Task.task_type == "yandex_visit", Task.status == "pending", Task.created_at > today
).scalar()
inprog = db.query(func.count(Task.id)).filter(
    Task.task_type == "yandex_visit", Task.status == "in_progress", Task.created_at > today
).scalar()
total = comp + fail
rate = comp * 100.0 / total if total else 0
print("Maps today: completed=%d, failed=%d, pending=%d, in_progress=%d, rate=%.1f%%" % (
    comp, fail, pend, inprog, rate))

# Error breakdown
errors = db.query(Task.error_message, func.count(Task.id)).filter(
    Task.task_type == "yandex_visit",
    Task.status == "failed",
    Task.created_at > today,
).group_by(Task.error_message).order_by(func.count(Task.id).desc()).limit(10).all()
print("\nTop errors:")
for err, cnt in errors:
    print("  %d x %s" % (cnt, str(err)[:100] if err else "none"))

# Check yandex_targets table - the /yandex-targets page
import sqlalchemy
meta = sqlalchemy.MetaData()
meta.reflect(bind=db.get_bind())
print("\nDB tables with 'target':")
for t in meta.tables:
    if "target" in t.lower():
        print("  %s" % t)

# Try to read yandex_search_targets
try:
    from app.models.yandex_search_target import YandexSearchTarget
    st = db.query(YandexSearchTarget).filter(YandexSearchTarget.is_active == True).all()
    print("\nActive search targets: %d" % len(st))
    for t in st:
        print("  #%d domain=%s visits_per_day=%s today=%s/%s" % (
            t.id, t.domain, t.visits_per_day,
            t.today_successful_visits if hasattr(t, 'today_successful_visits') else '?',
            t.today_visits if hasattr(t, 'today_visits') else '?'))
except Exception as e:
    print("Error: %s" % e)

# Check yandex_target table (maps targets)
try:
    from app.models.yandex_target import YandexMapsTarget
    mt = db.query(YandexMapsTarget).filter(YandexMapsTarget.is_active == True).all()
    print("\nActive maps targets: %d" % len(mt))
    for t in mt:
        print("  #%d %s visits_per_day=%s today=%s/%s" % (
            t.id,
            getattr(t, 'name', getattr(t, 'company_name', '?')),
            getattr(t, 'visits_per_day', '?'),
            getattr(t, 'today_successful', getattr(t, 'today_successful_visits', '?')),
            getattr(t, 'today_visits', '?')))
except Exception as e:
    print("Error with YandexMapsTarget: %s" % e)

# Raw SQL approach
try:
    result = db.execute(sqlalchemy.text("SELECT id, company_name, is_active, visits_per_day, today_visits, today_successful_visits FROM yandex_maps_targets WHERE is_active = true LIMIT 10"))
    rows = result.fetchall()
    print("\nRaw maps targets: %d" % len(rows))
    for r in rows:
        print("  #%d %s vpd=%s today=%s/%s" % (r[0], r[1], r[3], r[5], r[4]))
except Exception as e1:
    try:
        result = db.execute(sqlalchemy.text("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%%target%%'"))
        print("\nTarget tables: %s" % [r[0] for r in result.fetchall()])
    except:
        try:
            result = db.execute(sqlalchemy.text("SELECT tablename FROM pg_tables WHERE tablename LIKE '%%target%%'"))
            print("\nTarget tables: %s" % [r[0] for r in result.fetchall()])
        except Exception as e3:
            print("Cannot list tables: %s" % e3)

db.close()
