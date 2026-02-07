#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from celery.result import AsyncResult
from tasks.celery_app import celery_app

task_id = "3b1f0dad-1dd2-4dce-80e4-1aa94086f026"
result = AsyncResult(task_id, app=celery_app)

print(f"Task ID: {task_id}")
print(f"State: {result.state}")
print(f"Info: {result.info}")
if result.traceback:
    print(f"\nTraceback:\n{result.traceback}")
