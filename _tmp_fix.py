from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
# Fix zombie tasks: status=in_progress but completed_at is set
r = db.execute(text("UPDATE tasks SET status='failed' WHERE task_type='yandex_search' AND status='in_progress' AND completed_at IS NOT NULL"))
db.commit()
print("Fixed %d zombie tasks (in_progress with completed_at)" % r.rowcount)
db.close()
