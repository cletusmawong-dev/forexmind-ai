"""Health-check bridge slots 2-5 only.

The existing watchdog.py keeps watching port 8700. This process never
opens that port and never restarts the slot-1 terminal or service.

Each pass reads C:\\ForexMind\\secrets\\accounts\\N.env (or
/opt/forexmind/secrets/accounts/N.env), GETs 127.0.0.1:<port>/health
with that file's BRIDGE_TOKEN, and if the slot is down runs the
matching start-slotN command. A failure on one slot does not touch
the others.
"""
from __future__ import annotations

import os
import subprocess
import urllib.request
from pathlib import Path

SLOTS = {2: 8701, 3: 8702, 4: 8703, 5: 8704}


def _secret_dir() -> Path:
    for candidate in (
        Path(os.environ.get("FXM_SECRETS", "")),
        Path(r"C:\ForexMind\secrets\accounts"),
        Path("/opt/forexmind/secrets/accounts"),
    ):
        if str(candidate) and candidate.is_dir():
            return candidate
    return Path(r"C:\ForexMind\secrets\accounts")


def _parse_env(path: Path) -> dict:
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _health(port: int, token: str) -> bool:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/health",
        headers={"X-Bridge-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _start(slot: int) -> None:
    if os.name == "nt":
        cmd = Path(rf"C:\ForexMind\start-slot{slot}.cmd")
        if cmd.is_file():
            subprocess.Popen(["cmd", "/c", str(cmd)], close_fds=True)  # noqa: S603
        return
    unit = f"forexmind-bridge-slot{slot}"
    subprocess.call(["systemctl", "restart", unit])  # noqa: S603


def pass_once() -> dict:
    """One pass. Returns {slot: 'up'|'down'|'skipped'}."""
    results = {}
    secret_dir = _secret_dir()
    for slot, port in SLOTS.items():
        env = _parse_env(secret_dir / f"{slot}.env")
        token = env.get("BRIDGE_TOKEN", "")
        if not token or not env.get("MT5_LOGIN"):
            results[slot] = "skipped"
            continue
        if _health(port, token):
            results[slot] = "up"
            continue
        results[slot] = "down"
        try:
            _start(slot)
        except Exception:
            results[slot] = "down"
    return results


if __name__ == "__main__":
    print(pass_once())
