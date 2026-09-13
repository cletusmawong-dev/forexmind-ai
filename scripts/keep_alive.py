"""Keep the free-tier Render backend awake (also serves as an uptime ping).

GitHub Actions runs this every 10 minutes. The workflow lives in
.github/workflows/keep-alive.yml; this script just performs the ping with
friendly output.
"""
import os
import sys
import urllib.request

BASE = os.environ.get("APP_BASE_URL", "https://forexmind-ai-api.onrender.com")

try:
    with urllib.request.urlopen(f"{BASE}/api/health", timeout=25) as r:
        body = r.read().decode()[:120]
        print(f"[keep-alive] {r.status} {body}")
        sys.exit(0 if r.status == 200 else 1)
except Exception as e:
    print(f"[keep-alive] ping failed: {e}")
    sys.exit(1)
