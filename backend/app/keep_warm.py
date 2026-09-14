"""Keep-warm: the free-tier backend must stay awake to catch every candle.

Render sleeps a free service after ~15 min without inbound traffic. A
sleeping agent cannot scan, alert or execute, so this module makes the
service call ITSELF over the public URL every few minutes (the request
leaves the container and re-enters through Render's proxy, which counts
as traffic). Layered with the GitHub Actions pinger, idle gaps close.

Pure best-effort: every failure is swallowed and retried next cycle.
"""
from __future__ import annotations

import os
import threading
import time

import requests

_interval = int(os.getenv("KEEP_WARM_SECONDS", "300"))
_started = False
_ok = 0
_fail = 0


def _self_url() -> str:
    return (os.getenv("RENDER_EXTERNAL_URL")
            or os.getenv("APP_SELF_URL") or "").rstrip("/")


def _loop() -> None:
    global _ok, _fail
    time.sleep(90)   # let startup settle
    url = _self_url()
    if not url:
        return
    while True:
        try:
            r = requests.get(f"{url}/api/health", timeout=10)
            _ok += 1 if r.status_code == 200 else 0
            _fail += 0 if r.status_code == 200 else 1
        except Exception:
            _fail += 1
        time.sleep(max(60, _interval))


def start() -> None:
    """Idempotent: spawns the daemon pinger when a public URL is known."""
    global _started
    if _started or not _self_url():
        return
    _started = True
    threading.Thread(target=_loop, name="keep-warm", daemon=True).start()


def stats() -> dict:
    return {"url": _self_url() or None, "running": _started,
            "ok": _ok, "failed": _fail, "interval_s": max(60, _interval)}
