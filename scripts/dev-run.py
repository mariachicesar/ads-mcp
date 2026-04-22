import json
import os
import pathlib
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICES = [
    ("google-ads",    "servers/google-ads/main.py",    8001),
    ("meta-ads",      "servers/meta-ads/main.py",      8002),
    ("analytics",     "servers/analytics/main.py",     8003),
    ("search-console","servers/search-console/main.py",8004),
    ("content-agent", "servers/content-agent/main.py", 8005),
]

ROOT_PATH = pathlib.Path(ROOT)
dev_config_path = ROOT_PATH / "local-dev-config.json"

env = os.environ.copy()
env["ADS_MCP_REQUIRE_SIGNED_REQUESTS"] = "false"

if dev_config_path.exists():
    raw = json.loads(dev_config_path.read_text())

    # Extract top-level non-business keys before building per-service configs
    anthropic_key = raw.pop("anthropic_api_key", None)
    if anthropic_key and not env.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = anthropic_key

    # google-ads: keys match local-dev-config.json directly
    env["ADS_MCP_GOOGLE_ADS_CONFIGS_JSON"] = json.dumps(raw)

    # analytics: ga4_property_id → property_id
    analytics_configs = {
        bk: {**cfg, "property_id": cfg.get("ga4_property_id", "")}
        for bk, cfg in raw.items()
    }
    env["ADS_MCP_ANALYTICS_CONFIGS_JSON"] = json.dumps(analytics_configs)

    # search-console: keys match (site_url, client_id, client_secret, refresh_token)
    env["ADS_MCP_SEARCH_CONSOLE_CONFIGS_JSON"] = json.dumps(raw)

    print(f"  loaded local-dev-config.json ({len(raw)} business keys: {', '.join(raw.keys())})")  
else:
    print(f"  WARNING: {dev_config_path} not found — services will rely on Secrets Manager")

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