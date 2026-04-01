#!/usr/bin/env python3
"""Fix phone columns E and F — rewrite from DB with apostrophe prefix."""
import gspread
from google.oauth2.service_account import Credentials
import subprocess, csv, io

creds = Credentials.from_service_account_file(
    '/Users/sqrtv2/Downloads/sheets-sync-491810-e08aa0f9ec9e.json',
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key('1vxgEoGxl3IG6cCGcTWnWV_Z34PjFyuGQeIrJnzIk8VI')
ws = sh.sheet1

# Get DB phones
sql = "COPY (SELECT id, phone, phone2 FROM parsed_companies ORDER BY id) TO STDOUT WITH CSV HEADER"
cmd = ['ssh', 'root@88.99.146.218',
    "cd /root/yandex-maps-bot && docker compose exec -T postgres psql -U postgres -d yandex_maps_bot -c \"" + sql + "\""]
result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
reader = csv.reader(io.StringIO(result.stdout))
next(reader)

db_phones = {}
for row in reader:
    if len(row) >= 3:
        db_phones[int(row[0])] = (row[1] or '', row[2] or '')

print(f"DB phones: {len(db_phones)}")

# Get ordered DB IDs
sql2 = "COPY (SELECT id FROM parsed_companies ORDER BY id) TO STDOUT WITH CSV HEADER"
cmd2 = ['ssh', 'root@88.99.146.218',
    "cd /root/yandex-maps-bot && docker compose exec -T postgres psql -U postgres -d yandex_maps_bot -c \"" + sql2 + "\""]
result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=30)
reader2 = csv.reader(io.StringIO(result2.stdout))
next(reader2)
db_ids = [int(r[0]) for r in reader2]

# Build phone values
phone_vals = []
for db_id in db_ids:
    p1, p2 = db_phones.get(db_id, ('', ''))
    if p1 and p1[0] in ('+', '=', '-', '@'):
        p1 = "'" + p1
    if p2 and p2[0] in ('+', '=', '-', '@'):
        p2 = "'" + p2
    phone_vals.append([p1, p2])

print(f"Updating {len(phone_vals)} phone rows...")
rng = f'E2:F{len(phone_vals) + 1}'
ws.update(range_name=rng, values=phone_vals, value_input_option='USER_ENTERED')
print("Done!")
