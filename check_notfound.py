#!/usr/bin/env python3
"""Check not_found distribution by domain/keyword."""
from app.database import SessionLocal
from app.models.task import Task
from collections import Counter
from datetime import datetime
import json

db = SessionLocal()
today = datetime.utcnow().replace(hour=0, minute=0, second=0)
failed = db.query(Task).filter(
    Task.task_type == "yandex_search",
    Task.status == "failed",
    Task.created_at >= today
).all()

not_found_domains = Counter()
not_found_keywords = Counter()
for t in failed:
    err = str(t.error_message) if t.error_message else ""
    if "not found" in err.lower() or "not_found" in err.lower():
        params = t.parameters if isinstance(t.parameters, dict) else {}
        domain = params.get("domain", "?")
        keyword = params.get("keyword", "?")
        not_found_domains[domain] += 1
        not_found_keywords[f"{domain} | {keyword}"] += 1

print("=== NOT FOUND by domain ===")
for domain, count in not_found_domains.most_common(20):
    print(f"  {count:4d}  {domain}")

print()
print("=== Top NOT FOUND keyword+domain ===")
for combo, count in not_found_keywords.most_common(25):
    print(f"  {count:4d}  {combo}")

# Also check completed by domain
completed = db.query(Task).filter(
    Task.task_type == "yandex_search",
    Task.status == "completed",
    Task.created_at >= today
).all()

completed_domains = Counter()
for t in completed:
    params = t.parameters if isinstance(t.parameters, dict) else {}
    domain = params.get("domain", "?")
    completed_domains[domain] += 1

print()
print("=== COMPLETED by domain ===")
for domain, count in completed_domains.most_common(20):
    print(f"  {count:4d}  {domain}")

db.close()
