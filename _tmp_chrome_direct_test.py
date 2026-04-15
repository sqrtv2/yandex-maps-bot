"""Direct Chrome launch test - check if Chrome itself crashes."""
import subprocess, os, time

chrome = "/opt/pw-browsers/chromium-1169/chrome-linux/chrome"
profile = "/app/browser_profiles/Profile-26836"

# Clean locks
for f in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
    p = os.path.join(profile, f)
    if os.path.exists(p) or os.path.islink(p):
        os.remove(p)

# Set up pipe FDs 3 and 4 for Chrome debugging pipe
r1, w1 = os.pipe()
r2, w2 = os.pipe()

args = [
    chrome,
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-breakpad",
    "--no-first-run",
    "--disable-extensions",
    "--disable-features=TranslateUI",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--user-data-dir=" + profile,
    "--remote-debugging-pipe",
    "about:blank",
]

env = os.environ.copy()
env["DISPLAY"] = ":99"

def setup_fds():
    os.dup2(r1, 3)
    os.dup2(w2, 4)

proc = subprocess.Popen(
    args,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
    preexec_fn=setup_fds,
)
print("Chrome PID:", proc.pid)

# Close our copies of the child-side FDs
os.close(r1)
os.close(w2)

# Wait for Chrome to do something
time.sleep(3)

if proc.poll() is not None:
    _, stderr = proc.communicate(timeout=2)
    print("EXIT CODE:", proc.returncode)
    if proc.returncode < 0:
        import signal as sig
        signame = "UNKNOWN"
        try:
            signame = sig.Signals(-proc.returncode).name
        except (ValueError, AttributeError):
            pass
        print("KILLED BY SIGNAL:", -proc.returncode, signame)
    lines = stderr.decode(errors="replace").split("\n")
    non_dbus = [l for l in lines if "dbus" not in l.lower() and l.strip()]
    print("Non-dbus stderr (%d lines):" % len(non_dbus))
    for l in non_dbus[:20]:
        print("  ", l[:200])
else:
    print("Chrome ALIVE after 3s - SUCCESS")
    # Try to read from pipe to confirm CDP works
    try:
        import select
        ready = select.select([r2], [], [], 1)
        if ready[0]:
            data = os.read(r2, 4096)
            print("Read %d bytes from CDP pipe" % len(data))
        else:
            print("No CDP data on pipe (timeout)")
    except Exception as e:
        print("Pipe read error:", e)
    proc.kill()
    proc.wait()

os.close(w1)
os.close(r2)
print("Done")
