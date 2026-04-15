import sys
sys.path.insert(0, '.')

with open('mailing/default_template.html', 'r') as f:
    body_html = f.read()

from database import SessionLocal
db = SessionLocal()

db.execute("UPDATE mailing_campaigns SET body_html = :h, subject = :s, sender_name = :sn WHERE id = 1",
           {'h': body_html, 's': 'Предложение для {company_name}', 'sn': 'Анатолий'})
db.execute("UPDATE mailing_messages SET status = 'pending', sent_at = NULL, error_message = NULL WHERE campaign_id = 1")
db.execute("UPDATE mailing_campaigns SET status = 'draft', sent_count = 0, failed_count = 0 WHERE id = 1")
db.commit()
print('Campaign updated, messages reset')

row = db.execute("SELECT id, name, subject, sender_name, status FROM mailing_campaigns WHERE id = 1").fetchone()
print(f'Campaign: {row}')
msgs = db.execute("SELECT id, recipient_email, status FROM mailing_messages WHERE campaign_id = 1").fetchall()
for m in msgs:
    print(f'  Msg: {m}')
db.close()
