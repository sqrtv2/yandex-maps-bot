#!/usr/bin/env python3
"""Test: how to get Chrome PID from launch_persistent_context"""
import glob
import subprocess
import shutil

chromium_exe = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))[-1]
print(f"Using: {chromium_exe}")

from playwright.sync_api import sync_playwright
p = sync_playwright().start()
ctx = p.chromium.launch_persistent_context(
    "/tmp/test_pid3", headless=False, executable_path=chromium_exe,
    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
)

# 1. pgrep to find actual Chrome PIDs
r = subprocess.check_output(["pgrep", "-f", "test_pid3"]).decode().strip().split("\n")
print(f"Chrome PIDs from pgrep: {r}")

# 2. Check context for process/pid attrs
proc_attrs = [a for a in dir(ctx) if "proc" in a.lower() or "pid" in a.lower()]
print(f"context process attrs: {proc_attrs}")

# 3. Check Playwright internal transport for node-driver PID
try:
    conn = ctx._impl_obj._channel._connection
    transport = conn._transport
    if hasattr(transport, "_proc"):
        print(f"node-driver PID: {transport._proc.pid}")
        # The node-driver is parent of Chrome — we can find Chrome from here
        import psutil
        node = psutil.Process(transport._proc.pid)
        children = node.children(recursive=True)
        for c in children:
            print(f"  child: pid={c.pid} name={c.name()} cmdline={' '.join(c.cmdline())[:100]}")
    else:
        print(f"transport attrs: {[a for a in dir(transport) if not a.startswith('__')]}")
except Exception as e:
    print(f"Error inspecting internals: {e}")

ctx.close()
p.stop()
shutil.rmtree("/tmp/test_pid3", ignore_errors=True)
print("Done")
