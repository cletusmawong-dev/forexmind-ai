"""ForexMind VPS watchdog - one idempotent healing pass, then exits.

Task Scheduler runs this every minute (task "ForexMindWatchdog", created by
install.ps1). Each pass:

  1. bridge answering on 127.0.0.1:<port>/health with the token?
     - no  -> make sure MT5 terminal is running (start it if not), then
              start the bridge via pythonw (detached)
  2. bridge up but terminal not connected / trade_allowed off -> log it
  3. every action and every pass is appended to vps\\watchdog.log

Stdlib only - no extra packages on the VPS. Never raises in normal
operation; unexpected errors are logged, not crashed.

Env / secrets (first hit wins):
  BRIDGE_TOKEN / BRIDGE_PORT / MT5_TERMINAL_EXE from the process env,
  else vps\\secrets.env, else vps-bridge\\.env  (KEY=VALUE lines)
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
BRIDGE_DIR = REPO / "vps-bridge"
BRIDGE_PY = BRIDGE_DIR / "bridge.py"
LOG = HERE / "watchdog.log"
LOG_MAX_LINES = 500
HEALTH_TIMEOUT_S = 6


# ---------------------------------------------------------------- helpers
def _load_kv(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _secrets() -> dict:
    merged = {}
    for p in (BRIDGE_DIR / ".env", HERE / "secrets.env"):
        merged.update(_load_kv(p))
    for k in ("BRIDGE_TOKEN", "BRIDGE_PORT", "MT5_TERMINAL_EXE"):
        if os.environ.get(k):
            merged[k] = os.environ[k]
    return merged


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    try:
        if LOG.exists():
            tail = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
            if len(tail) > LOG_MAX_LINES:
                tail = tail[-LOG_MAX_LINES:]
                tail.append(line)
                LOG.write_text("\n".join(tail) + "\n", encoding="utf-8")
                print(line)
                return
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


def _find_terminal_exe(sec: dict) -> str:
    exe = (sec.get("MT5_TERMINAL_EXE") or "").strip()
    if exe and Path(exe).exists():
        return exe
    for pat in (r"C:\Program Files\*MetaTrader*\terminal64.exe",
                r"C:\Program Files (x86)\*MetaTrader*\terminal64.exe"):
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return ""


def _terminal_running() -> bool:
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe",
                            "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=15)
        return "terminal64" in (r.stdout or "").lower()
    except Exception as exc:
        log(f"WARN terminal process check failed: {type(exc).__name__}")
        return False


def _start_terminal(exe: str) -> None:
    try:
        subprocess.Popen([exe], close_fds=True)
        log(f"STARTED MT5 terminal: {exe}")
    except Exception as exc:
        log(f"ERROR starting terminal {exe}: {type(exc).__name__}")


def _start_bridge(token: str, port: str) -> None:
    import shutil
    pyw = shutil.which("pythonw") or shutil.which("python")
    if not pyw:
        log("ERROR python/pythonw not on PATH - cannot start bridge")
        return
    env = dict(os.environ)
    env["BRIDGE_TOKEN"] = token
    if port:
        env["BRIDGE_PORT"] = port
    try:
        flags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([pyw, str(BRIDGE_PY)], cwd=str(BRIDGE_DIR),
                         env=env, close_fds=True, creationflags=flags)
        log(f"STARTED bridge ({pyw} bridge.py) on port {port or '8700'}")
    except Exception as exc:
        log(f"ERROR starting bridge: {type(exc).__name__}")


def _health(token: str, port: str) -> dict:
    url = f"http://127.0.0.1:{port or '8700'}/health"
    req = urllib.request.Request(url, headers={"X-Bridge-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        return {"_down": f"{type(exc).__name__}"}


# ---------------------------------------------------------------- one pass
def main() -> int:
    if sys.platform != "win32":
        print("watchdog: Windows-only (MT5 host). Nothing to do on this OS.")
        return 0

    sec = _secrets()
    token = sec.get("BRIDGE_TOKEN", "")
    if not token or token.startswith("paste-") or token == "change-me":
        log("ERROR BRIDGE_TOKEN not set (machine env, vps\\secrets.env or vps-bridge\\.env)")
        return 0
    port = sec.get("BRIDGE_PORT", "8700")

    h = _health(token, port)
    if h.get("_down"):
        log(f"bridge DOWN ({h['_down']}) - healing")
        if not _terminal_running():
            exe = _find_terminal_exe(sec)
            if exe:
                _start_terminal(exe)
            else:
                log("WARN terminal64.exe not found - set MT5_TERMINAL_EXE")
        _start_bridge(token, port)
        return 0

    term = h.get("terminal") or {}
    acct = h.get("account") or {}
    if not term.get("connected"):
        log("bridge UP but MT5 terminal NOT connected to broker")
        if not _terminal_running():
            exe = _find_terminal_exe(sec)
            if exe:
                _start_terminal(exe)
        return 0
    if not term.get("trade_allowed"):
        log("WARN MT5 trade_allowed=false - enable Algo Trading in the terminal")
        return 0
    login = acct.get("login", "?")
    bal = acct.get("balance", "?")
    log(f"OK bridge up, MT5 connected, account {login} balance {bal}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:            # absolute last resort - never crash loud
        log(f"FATAL {type(exc).__name__}: {exc}")
        sys.exit(0)
