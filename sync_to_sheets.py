#!/usr/bin/env python3
"""
Sync parsed companies from DB to Google Sheets.
Only appends new rows — existing rows are never modified.

Usage:
    python3 sync_to_sheets.py                  # sync all
    python3 sync_to_sheets.py --query "медцентр"  # sync specific query

Unique key: yandex_maps_id (or name+address fallback).
"""

import argparse
import os
import sys
import time

import gspread
from google.oauth2.service_account import Credentials

# --- Config ---
SPREADSHEET_ID = '1vxgEoGxl3IG6cCGcTWnWV_Z34PjFyuGQeIrJnzIk8VI'
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__),
    os.environ.get('GOOGLE_CREDS', '/Users/sqrtv2/Downloads/sheets-sync-491810-e08aa0f9ec9e.json'))

HEADERS = [
    '№', 'Название', 'Категория', 'Адрес', 'Телефон', 'Телефон 2',
    'Email', 'Сайт', 'Telegram', 'WhatsApp', 'VK', 'Instagram',
    'Рейтинг', 'Отзывов', 'Часы работы', 'Запрос', 'Регион',
    'Ссылка Яндекс.Карты'
]

# DB column mapping (matches HEADERS order, minus №)
DB_COLUMNS = [
    'name', 'category', 'address', 'phone', 'phone2',
    'email', 'website', 'telegram', 'whatsapp', 'vk', 'instagram',
    'rating', 'reviews_count', 'working_hours', 'search_query', 'region',
    'yandex_maps_url'
]


def get_db_data(query_filter=None):
    """Fetch parsed companies from the server DB via SSH + psql as CSV."""
    import subprocess
    import csv
    import io

    # Build SQL
    cols = ', '.join(['id', 'yandex_maps_id'] + DB_COLUMNS)
    sql = f"COPY (SELECT {cols} FROM parsed_companies"
    if query_filter:
        safe_q = query_filter.replace("'", "''")
        sql += f" WHERE search_query = '{safe_q}'"
    sql += " ORDER BY id) TO STDOUT WITH CSV HEADER"

    cmd = [
        'ssh', 'root@88.99.146.218',
        f"cd /root/yandex-maps-bot && docker compose exec -T postgres "
        f"psql -U postgres -d yandex_maps_bot -c \"{sql}\""
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"DB error: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    reader = csv.reader(io.StringIO(result.stdout))
    header = next(reader, None)  # skip CSV header
    if not header:
        return []

    rows = []
    for parts in reader:
        if len(parts) >= len(DB_COLUMNS) + 2:
            rows.append(parts)
    return rows


def build_unique_key(row):
    """Build unique key from DB row: prefer yandex_maps_id, fallback to name+address."""
    db_id = row[0]
    yandex_maps_id = row[1] if len(row) > 1 else ''
    name = row[2] if len(row) > 2 else ''
    address = row[4] if len(row) > 4 else ''

    if yandex_maps_id and yandex_maps_id.strip():
        return f"ymid:{yandex_maps_id.strip()}"
    return f"na:{name.strip()}|{address.strip()}"


def db_row_to_sheet_row(row, row_num):
    """Convert DB row to sheet row (matching HEADERS order)."""
    # row[0]=id, row[1]=yandex_maps_id, row[2:]=DB_COLUMNS
    values = row[2:]  # skip id and yandex_maps_id
    sheet_row = [str(row_num)]  # №
    for i, val in enumerate(values):
        if not val or val == '\\N':
            sheet_row.append('')
        elif i in (3, 4) and val[0] in ('+', '=', '-', '@'):
            # Phone/Phone2: prefix with apostrophe to prevent formula interpretation
            sheet_row.append("'" + val)
        else:
            sheet_row.append(val)
    # Pad if needed
    while len(sheet_row) < len(HEADERS):
        sheet_row.append('')
    return sheet_row[:len(HEADERS)]


def sheet_row_unique_key(row):
    """Build unique key from existing sheet row."""
    if len(row) < 18:
        row = row + [''] * (18 - len(row))

    name = row[1].strip() if row[1] else ''        # Название
    address = row[3].strip() if row[3] else ''      # Адрес
    ymaps_url = row[17].strip() if row[17] else ''  # Ссылка Яндекс.Карты

    # Extract yandex_maps_id from URL if possible
    if ymaps_url and '/oid/' in ymaps_url:
        oid = ymaps_url.split('/oid/')[-1].split('/')[0].split('?')[0]
        if oid:
            return f"ymid:{oid}"

    return f"na:{name}|{address}"


def sync(query_filter=None):
    print("Connecting to Google Sheets...")
    creds = Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.sheet1

    # Read existing data
    existing = ws.get_all_values()
    print(f"Sheet has {len(existing)} rows (including header)")

    # If empty sheet, write headers first
    if not existing or not existing[0] or not existing[0][0]:
        print("Writing headers...")
        ws.update('A1', [HEADERS])
        existing = [HEADERS]
        time.sleep(1)

    # Build set of existing keys
    existing_keys = set()
    for row in existing[1:]:  # skip header
        key = sheet_row_unique_key(row)
        existing_keys.add(key)

    print(f"Existing unique entries: {len(existing_keys)}")

    # Fetch DB data
    print("Fetching data from DB...")
    db_rows = get_db_data(query_filter)
    print(f"DB rows: {len(db_rows)}")

    # Find new rows
    new_rows = []
    next_num = len(existing)  # next row number (1-based, header is row 1)
    for row in db_rows:
        key = build_unique_key(row)
        if key not in existing_keys:
            next_num += 1
            sheet_row = db_row_to_sheet_row(row, next_num - 1)
            new_rows.append(sheet_row)
            existing_keys.add(key)

    if not new_rows:
        print("No new rows to add.")
        return

    print(f"Appending {len(new_rows)} new rows...")

    # Batch append (gspread handles chunking)
    BATCH_SIZE = 500
    for i in range(0, len(new_rows), BATCH_SIZE):
        batch = new_rows[i:i + BATCH_SIZE]
        ws.append_rows(batch, value_input_option='USER_ENTERED')
        print(f"  Appended {min(i + BATCH_SIZE, len(new_rows))}/{len(new_rows)}")
        if i + BATCH_SIZE < len(new_rows):
            time.sleep(2)  # rate limit

    print(f"Done! Total rows in sheet: {len(existing) + len(new_rows)}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sync parsed companies to Google Sheets')
    parser.add_argument('--query', '-q', help='Filter by search_query')
    args = parser.parse_args()
    sync(query_filter=args.query)
