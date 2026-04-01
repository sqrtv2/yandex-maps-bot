from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile
from app.models.task import Task
from sqlalchemy import func

db = SessionLocal()

total = db.query(func.count(BrowserProfile.id)).scalar()
warmed = db.query(func.count(BrowserProfile.id)).filter(BrowserProfile.warmup_completed == True).scalar()
warming = db.query(func.count(BrowserProfile.id)).filter(BrowserProfile.status == 'warming_up').scalar()
created = db.query(func.count(BrowserProfile.id)).filter(BrowserProfile.status == 'created').scalar()
active = db.query(func.count(BrowserProfile.id)).filter(BrowserProfile.is_active == True).scalar()

stages = db.query(BrowserProfile.warmup_stage, func.count(BrowserProfile.id)).group_by(BrowserProfile.warmup_stage).all()
statuses = db.query(BrowserProfile.status, func.count(BrowserProfile.id)).group_by(BrowserProfile.status).all()

print("=== Profile Stats ===")
print(f"Total: {total}")
print(f"Active: {active}")
print(f"Warmed (completed): {warmed}")
print(f"Warming up now: {warming}")
print(f"Created (waiting): {created}")
print()
print("By status:")
for s, c in statuses:
    print(f"  {s}: {c}")
print()
print("By warmup_stage:")
for s, c in sorted(stages):
    print(f"  stage {s}: {c}")
print()

running = db.query(func.count(Task.id)).filter(Task.task_type == 'warmup', Task.status == 'in_progress').scalar()
pending = db.query(func.count(Task.id)).filter(Task.task_type == 'warmup', Task.status == 'pending').scalar()
print(f"Warmup tasks running: {running}")
print(f"Warmup tasks pending: {pending}")

not_started = db.query(func.count(BrowserProfile.id)).filter(BrowserProfile.warmup_stage == 0, BrowserProfile.is_active == True).scalar()
s1 = db.query(func.count(BrowserProfile.id)).filter(BrowserProfile.warmup_stage == 1, BrowserProfile.is_active == True).scalar()
s2 = db.query(func.count(BrowserProfile.id)).filter(BrowserProfile.warmup_stage == 2, BrowserProfile.is_active == True).scalar()
print(f"Active stage 0 (not started): {not_started}")
print(f"Active stage 1: {s1}")
print(f"Active stage 2: {s2}")
db.close()
