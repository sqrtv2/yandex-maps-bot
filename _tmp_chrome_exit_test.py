"""Test Chrome launch exit code - run inside maps container."""
import subprocess, os, time

profile_dir = "/app/browser_profiles/Profile-26836"
chrome_bin = "/opt/pw-browsers/chromium-1169/chrome-linux/chrome"

# Clean singleton locks
for f in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
    p = os.path.join(profile_dir, f)
    if os.path.exists(p) or os.path.islink(p):
        os.remove(p)

args = [
    chrome_bin,
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-breakpad",
    "--no-first-run",
    "--disable-extensions",
    "--disable-features=AcceptCHFrame,DestroyProfileOnBrowserClose,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,Translate",
    "--disable-hang-monitor",
    "--disable-popup-blocking",
    "--disable-renderer-backgrounding",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--js-flags=--max-old-space-size=1024",
    "--user-data-dir=" + profile_dir,
    "--remote-debugging-pipe",
    "about:blank",
]

env = os.environ.copy()
env["DISPLAY"] = ":99"

print("Launching Chrome...", time.strftime("%H:%M:%S"))
proc = subprocess.Popen(
    args,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
)
print("Chrome PID:", proc.pid)

try:
    stdout, stderr = proc.communicate(timeout=5)
    print("Chrome EXITED with code:", proc.returncode)
    if proc.returncode < 0:
        print("Killed by signal:", -proc.returncode)
    stderr_txt = stderr.decode(errors="replace")
    non_dbus = [l for l in stderr_txt.split("\n") if "dbus" not in l.lower() and l.strip()]
    if non_dbus:
        print("Non-dbus stderr (%d lines):" % len(non_dbus))
        for l in non_dbus[:20]:
            print("  ", l)
    else:
        print("Stderr: only dbus errors (normal)")
except subprocess.TimeoutExpired:
    print("Chrome still running after 5s (good - no crash)")
    proc.kill()
    proc.wait()
    print("Done")
