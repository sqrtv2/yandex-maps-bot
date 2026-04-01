#!/usr/bin/env python3
"""
Server-side sync: parsed_companies -> Google Sheets.
Only appends new rows. Existing rows are never modified.
Runs on server with direct postgres access.
"""

import argparse
import os
import sys
import time
import logging

import psycopg2
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
log = logging.getLogger('sheets_sync')

# --- Config ---
SPREADSHEET_ID = '1vxgEoGxl3IG6cCGcTWnWV_Z34PjFyuGQeIrJnzIk8VI'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, 'google_creds.json')
DB_URL = 'postgresql://postgres:password@172.18.0.2:5432/yandex_maps_bot'

HEADERS = [
    '\u2116', '\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435',
    '\u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f',
    '\u0410\u0434\u0440\u0435\u0441',
    '\u0422\u0435\u043b\u0435\u0444\u043e\u043d',
    '\u0422\u0435\u043b\u0435\u0444\u043e\u043d 2',
    'Email',
    '\u0421\u0430\u0439\u0442',
    'Telegram', 'WhatsApp', 'VK', 'Instagram',
    '\u0420\u0435\u0439\u0442\u0438\u043d\u0433',
    '\u041e\u0442\u0437\u044b\u0432\u043e\u0432',
    '\u0427\u0430\u0441\u044b \u0440\u0430\u0431\u043e\u0442\u044b',
    '\u0417\u0430\u043f\u0440\u043e\u0441',
    '\u0420\u0435\u0433\u0438\u043e\u043d',
    '\u0421\u0441\u044b\u043b\u043a\u0430 \u042f\u043d\u0434\u0435\u043a\u0441.\u041a\u0430\u0440\u0442\u044b'
]

DB_COLUMNS = [
    'name', 'category', 'address', 'phone', 'phone2',
    'email', 'website', 'telegram', 'whatsapp', 'vk', 'instagram',
    'rating', 'reviews_count', 'working_hours', 'search_query', 'region',
    'yandex_maps_url'
]


def get_db_data(query_filter=None):
    cols = ', '.join(['id', 'yandex_maps_id'] + DB_COLUMNS)
    sql = f"SELECT {cols} FROM parsed_companies"
    params = []
    if query_filter:
        sql += " WHERE search_query = %s"
        params.append(query_filter)
    sql += " ORDER BY id"

    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    return [[str(v) if v is not None else '' for v in row] for row in rows]


def build_unique_key(row):
    yandex_maps_id = row[1] if len(row) > 1 else ''
    name = row[2] if len(row) > 2 else ''
    address = row[4] if len(row) > 4 else ''
    if yandex_maps_id and yandex_maps_id.strip():
        return f"ymid:{yandex_maps_id.strip()}"
    return f"na:{name.strip()}|{address.strip()}"


def db_row_to_sheet_row(row, row_num):
    values = row[2:]
    sheet_row = [str(row_num)]
    for i, val in enumerate(values):
        if not val:
            sheet_row.append('')
        elif i in (3, 4) and val and val[0] in ('+', '=', '-', '@'):
            # Phone/Phone2: prefix with apostrophe to prevent formula interpretation
            sheet_row.append("'" + val)
        else:
            sheet_row.append(val)
    while len(sheet_row) < len(HEADERS):
        sheet_row.append('')
    return sheet_row[:len(HEADERS)]


def sheet_row_unique_key(row):
    import re
    if len(row) < 18:
        row = row + [''] * (18 - len(row))
    name = row[1].strip() if row[1] else ''
    address = row[3].strip() if row[3] else ''
    ymaps_url = row[17].strip() if row[17] else ''
    if ymaps_url:
        # Extract numeric ID from URL like /org/name/1349578037/
        m = re.search(r'/org/[^/]+/(\d+)', ymaps_url)
        if m:
            return f"ymid:{m.group(1)}"
    return f"na:{name}|{address}"


def sync(query_filter=None):
    log.info("Connecting to Google Sheets...")
    creds = Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.sheet1

    existing = ws.get_all_values()
    log.info(f"Sheet has {len(existing)} rows (including header)")

    if not existing or not existing[0] or not existing[0][0]:
        log.info("Writing headers...")
        ws.update(range_name='A1', values=[HEADERS])
        existing = [HEADERS]
        time.sleep(1)

    existing_keys = set()
    for row in existing[1:]:
        existing_keys.add(sheet_row_unique_key(row))
    log.info(f"Existing unique entries: {len(existing_keys)}")

    log.info("Fetching data from DB...")
    db_rows = get_db_data(query_filter)
    log.info(f"DB rows: {len(db_rows)}")

    new_rows = []
    next_num = len(existing)
    for row in db_rows:
        key = build_unique_key(row)
        if key not in existing_keys:
            next_num += 1
            sheet_row = db_row_to_sheet_row(row, next_num - 1)
            new_rows.append(sheet_row)
            existing_keys.add(key)

    if not new_rows:
        log.info("No new rows to add.")
        return

    log.info(f"Appending {len(new_rows)} new rows...")
    BATCH_SIZE = 500
    for i in range(0, len(new_rows), BATCH_SIZE):
        batch = new_rows[i:i + BATCH_SIZE]
        ws.append_rows(batch, value_input_option='USER_ENTERED')
        log.info(f"  Appended {min(i + BATCH_SIZE, len(new_rows))}/{len(new_rows)}")
        if i + BATCH_SIZE < len(new_rows):
            time.sleep(2)

    log.info(f"Done! Total rows in sheet: {len(existing) + len(new_rows)}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--query', '-q', help='Filter by search_query')
    args = parser.parse_args()
    sync(query_filter=args.query)
