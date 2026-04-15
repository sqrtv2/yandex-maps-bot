"""Import domains from backorder.ru CSV into the database."""
import sys
sys.path.insert(0, '/app')

import requests
import csv
import io
from app.database import get_db_session
from app.models.drop_domain import DropDomain


def safe_int(val, default=0):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def main():
    url = "https://backorder.ru/csv/?order=desc&tomorrow=1&by=hotness&page=1&items=50"
    print("Downloading CSV...")
    resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    print(f"Status: {resp.status_code}, size: {len(resp.content)} bytes")

    content = resp.content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(content), delimiter=";")
    rows = list(reader)
    print(f"Total rows: {len(rows)}")

    batch_date = rows[0].get("delete_date", "") if rows else ""
    print(f"Batch date: {batch_date}")

    with get_db_session() as db:
        existing = {
            d.domain for d in
            db.query(DropDomain.domain).filter(DropDomain.batch_date == batch_date).all()
        }
        print(f"Already in DB for this batch: {len(existing)}")

        imported = 0
        skipped = 0
        for row in rows:
            domain = (row.get("domainname") or "").strip()
            if not domain or domain in existing:
                skipped += 1
                continue
            if not (domain.endswith(".ru") or domain.endswith(".рф") or ".xn--" in domain):
                skipped += 1
                continue

            d = DropDomain(
                domain=domain,
                hotness=safe_int(row.get("hotness")),
                price=safe_int(row.get("price")),
                yandex_tic=safe_int(row.get("yandex_tic")),
                links=safe_int(row.get("links")),
                visitors=safe_int(row.get("visitors"), -1),
                domain_age=safe_int(row.get("old")),
                delete_date=row.get("delete_date", ""),
                registrar=row.get("registrar", ""),
                batch_date=batch_date,
            )
            db.add(d)
            existing.add(domain)
            imported += 1
            if imported % 500 == 0:
                db.commit()
                print(f"  committed {imported}...")

        db.commit()
        print(f"Done! Imported: {imported}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
