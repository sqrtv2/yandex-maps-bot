from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile
from sqlalchemy import func, desc
from datetime import datetime, timedelta

db = SessionLocal()
now = datetime.utcnow()
print(f"Now: {now}")
print()

warming = db.query(BrowserProfile).filter(BrowserProfile.status == 'warming_up').all()
print(f"Profiles in warming_up status: {len(warming)}")
for p in warming[:10]:
    age = (now - p.updated_at).total_seconds() / 60 if p.updated_at else 0
    print(f"  id={p.id} stage={p.warmup_stage} updated_at={p.updated_at} ({age:.0f}min ago)")
print()

created_not_done = db.query(func.count(BrowserProfile.id)).filter(
    BrowserProfile.status == 'created',
    BrowserProfile.warmup_completed == False
).scalar()
warmed = db.query(func.count(BrowserProfile.id)).filter(BrowserProfile.warmup_completed == True).scalar()
print(f"Created (awaiting warmup): {created_not_done}")
print(f"Warmed (complete): {warmed}")
print()

# Eligible for scheduling (cooldown passed)
threshold = now - timedelta(hours=0.25)
eligible = db.query(BrowserProfile).filter(
    BrowserProfile.warmup_completed == False,
    BrowserProfile.is_active == True,
    BrowserProfile.status == 'created',
    BrowserProfile.warmup_stage < 4,
).all()

eligible_ready = 0
eligible_cooldown = 0
for p in eligible:
    if p.last_used_at is None or p.last_used_at < threshold:
        eligible_ready += 1
    else:
        eligible_cooldown += 1

print(f"Eligible NOW (cooldown passed): {eligible_ready}")
print(f"In cooldown (last_used < 15min ago): {eligible_cooldown}")
print()

# Show some profiles with their last_used_at
samples = db.query(BrowserProfile).filter(
    BrowserProfile.is_active == True,
    BrowserProfile.warmup_completed == False
).order_by(BrowserProfile.warmup_stage.desc()).limit(10).all()
print("Sample profiles:")
for p in samples:
    lu = p.last_used_at
    age_str = f"{(now - lu).total_seconds() / 60:.0f}min ago" if lu else "never"
    print(f"  id={p.id} stage={p.warmup_stage} status={p.status} last_used={age_str}")

db.close()
