import sys
sys.path.insert(0, '/app')

from sqlalchemy import text
from app.database import SessionLocal
db = SessionLocal()

# Mark campaign as sending
db.execute(text("UPDATE mailing_campaigns SET status = 'sending' WHERE id = 1"))
db.commit()
db.close()

# Trigger celery task
from mailing.tasks import run_campaign_task
result = run_campaign_task.delay(1)
print(f"Task submitted: {result.id}")
