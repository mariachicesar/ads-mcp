import subprocess
import sys
import os
import time
import signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICES = [
    ("google-ads",    "servers/google-ads/main.py",    8001),
    ("meta-ads",      "servers/meta-ads/main.py",      8002),
    ("analytics",     "servers/analytics/main.py",     8003),
    ("search-console","servers/search-console/main.py",8004),
    ("content-agent", "servers/content-agent/main.py", 8005),
]

env = os.environ.copy()
env["ADS_MCP_REQUIRE_SIGNED_REQUESTS"] = "false"

procs = []
for name, path, port in SERVICES:
    p = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, path)],
        env=env,
        cwd=ROOT,
    )
    procs.append((name, port, p))
    print(f"  started {name} on :{port} (pid {p.pid})")

print("\nAll services running. Ctrl+C to stop.\n")

def shutdown(sig, frame):
    print("\nStopping...")
    for name, port, p in procs:
        p.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

for name, port, p in procs:
    p.wait()