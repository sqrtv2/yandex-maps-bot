#!/usr/bin/env python3
"""Test: does shared Playwright spawn one or multiple node-drivers?"""
import glob
import subprocess

chromium_exe = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))[-1]
print(f"Using: {chromium_exe}")

from playwright.sync_api import sync_playwright

# One shared Playwright instance
p = sync_playwright().start()

# Launch TWO persistent contexts
ctx1 = p.chromium.launch_persistent_context(
    "/tmp/test_multi1", headless=False, executable_path=chromium_exe,
    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
)
ctx2 = p.chromium.launch_persistent_context(
    "/tmp/test_multi2", headless=False, executable_path=chromium_exe,
    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
)

# Check node-driver count
import os
r = os.popen("ps aux | grep run-driver | grep -v grep").read().strip()
print(f"Node-driver processes:\n{r}")
nd_count = len([l for l in r.split('\n') if l.strip()])
print(f"Node-driver count: {nd_count}")

# Check per-context transport
try:
    t1 = ctx1._impl_obj._channel._connection._transport
    t2 = ctx2._impl_obj._channel._connection._transport
    pid1 = t1._proc.pid if hasattr(t1, '_proc') else None
    pid2 = t2._proc.pid if hasattr(t2, '_proc') else None
    print(f"ctx1 transport PID: {pid1}")
    print(f"ctx2 transport PID: {pid2}")
    print(f"Same transport? {pid1 == pid2}")
except Exception as e:
    print(f"Error: {e}")

# Chrome per context
r1 = subprocess.check_output(["pgrep", "-f", "test_multi1"]).decode().strip().split("\n")
r2 = subprocess.check_output(["pgrep", "-f", "test_multi2"]).decode().strip().split("\n")
print(f"Chrome for ctx1: {len(r1)} pids")
print(f"Chrome for ctx2: {len(r2)} pids")

ctx1.close()
ctx2.close()
p.stop()

import shutil
shutil.rmtree("/tmp/test_multi1", ignore_errors=True)
shutil.rmtree("/tmp/test_multi2", ignore_errors=True)
print("Done")
