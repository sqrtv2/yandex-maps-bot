#!/usr/bin/env python3
import sys, logging
sys.path.insert(0, "/app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from tasks.drop_domains import check_single_domain
result = check_single_domain(domain_id=2)
print("=== RESULT ===")
for k, v in result.items():
    print(f"  {k}: {v}")
