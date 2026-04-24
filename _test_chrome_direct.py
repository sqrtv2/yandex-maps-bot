"""Direct Chrome launch — bypass Playwright completely. See raw stderr."""
import time, glob, subprocess

PROXY = "http://sqrtv2:21607141@95.31.170.9:4102"
chrome = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))[-1]

def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)

log(f"chrome={chrome}")

proc = subprocess.Popen([
    chrome,
    "--no-sandbox", "--disable-dev-shm-usage",
    "--user-data-dir=/tmp/t4_prof",
    f"--proxy-server={PROXY}",
    "--headless=new",
    "--enable-logging=stderr", "--v=1",
    "--virtual-time-budget=15000",
    "--dump-dom",
    "https://example.com/"
], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

try:
    out, err = proc.communicate(timeout=30)
    log(f"exit={proc.returncode} stdout_size={len(out)} stderr_size={len(err)}")
    log("--- STDOUT first 500 ---")
    print(out[:500].decode(errors="replace"))
    log("--- STDERR last 4000 ---")
    print(err[-4000:].decode(errors="replace"))
except subprocess.TimeoutExpired:
    log("TIMEOUT — killing")
    proc.kill()
    out, err = proc.communicate()
    log("--- STDERR last 4000 ---")
    print(err[-4000:].decode(errors="replace"))
