<#
.SYNOPSIS
  ForexMind VPS one-time setup (Windows): deps, firewall, env, auto-start tasks.
.DESCRIPTION
  Run ONCE in an elevated PowerShell inside the repo's vps folder:
      Set-ExecutionPolicy -Scope Process Bypass -Force
      .\install.ps1 -BridgeToken "a-long-random-secret"
  Creates:
    - machine env vars BRIDGE_TOKEN / BRIDGE_PORT / MT5_TERMINAL_EXE
    - firewall rule "ForexMind Bridge" (inbound TCP <port>)
    - scheduled task ForexMindBridge   (at startup, SYSTEM)
    - scheduled task ForexMindWatchdog (every minute, SYSTEM)
  Idempotent: safe to re-run (tasks/rules are recreated).
#>
param(
  [string]$BridgeToken = "",
  [int]$BridgePort = 8700,
  [string]$TerminalExe = ""
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $pr = New-Object Security.Principal.WindowsPrincipal($id)
  if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: run this from an elevated (Administrator) PowerShell." -ForegroundColor Red
    exit 1
  }
}

$here     = $PSScriptRoot
$repo     = Split-Path -Parent $here
$bridge   = Join-Path $repo "vps-bridge"
$bridgePy = Join-Path $bridge "bridge.py"
$watchdog = Join-Path $here "watchdog.py"

Assert-Admin

# --- 1. token -------------------------------------------------------------
if (-not $BridgeToken -or $BridgeToken -lt 16) {
  $BridgeToken = Read-Host "Enter a BRIDGE_TOKEN (long random secret, 16+ chars)"
  if (-not $BridgeToken -or $BridgeToken.Length -lt 16) {
    Write-Host "ERROR: token must be 16+ characters." -ForegroundColor Red
    exit 1
  }
}

# --- 2. python ------------------------------------------------------------
$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "ERROR: python.exe not on PATH - install Python 3.11+ with 'Add to PATH'." -ForegroundColor Red; exit 1 }
Write-Host "python: $py"
& $py -m pip install -r (Join-Path $bridge "requirements.txt") | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: pip install failed." -ForegroundColor Red; exit 1 }
$pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }

# --- 3. machine env vars ----------------------------------------------------
[Environment]::SetEnvironmentVariable("BRIDGE_TOKEN", $BridgeToken, "Machine")
[Environment]::SetEnvironmentVariable("BRIDGE_PORT", "$BridgePort", "Machine")
if (-not $TerminalExe) {
  $cand = Get-ChildItem "C:\Program Files\*MetaTrader*\terminal64.exe" -ErrorAction SilentlyContinue |
          Select-Object -First 1
  if ($cand) { $TerminalExe = $cand.FullName }
}
if ($TerminalExe) {
  [Environment]::SetEnvironmentVariable("MT5_TERMINAL_EXE", $TerminalExe, "Machine")
  Write-Host "MT5 terminal: $TerminalExe"
} else {
  Write-Host "WARN: MT5 terminal64.exe not found - set MT5_TERMINAL_EXE later." -ForegroundColor Yellow
}
Write-Host "machine env: BRIDGE_TOKEN / BRIDGE_PORT set."

# --- 4. firewall ------------------------------------------------------------
netsh advfirewall firewall delete rule name="ForexMind Bridge" | Out-Null
netsh advfirewall firewall add rule name="ForexMind Bridge" dir=in action=allow `
  protocol=TCP localport=$BridgePort | Out-Null
Write-Host "firewall: inbound TCP $BridgePort allowed ('ForexMind Bridge')."

# --- 5. scheduled tasks ------------------------------------------------------
$trBridge   = "`"$pyw`" `"$bridgePy`""
$trWatchdog = "`"$pyw`" `"$watchdog`""
schtasks /Create /F /TN "ForexMindBridge"   /SC ONSTART /RU SYSTEM /TR $trBridge | Out-Null
schtasks /Create /F /TN "ForexMindWatchdog" /SC MINUTE /MO 1 /RU SYSTEM /TR $trWatchdog | Out-Null
Write-Host "tasks: ForexMindBridge (boot) + ForexMindWatchdog (every 1 min) created."

# --- 6. start now + verify ----------------------------------------------------
schtasks /Run /TN "ForexMindBridge" | Out-Null
Start-Sleep -Seconds 6
$health = & curl.exe -s -m 8 -H "X-Bridge-Token: $BridgeToken" "http://127.0.0.1:$BridgePort/health"
if ($health -match '"ok"\s*:\s*true') {
  Write-Host "HEALTH CHECK PASSED: $health" -ForegroundColor Green
} else {
  Write-Host "bridge did not answer yet (it may still be booting): $health" -ForegroundColor Yellow
  Write-Host "Reboot the VPS and re-check; also run: schtasks /Run /TN ForexMindWatchdog"
}

Write-Host ""
Write-Host "Setup complete. Next steps:"
Write-Host "  1. Reboot once; verify health WITHOUT logging in:  curl.exe -s -H ""X-Bridge-Token: $BridgeToken"" http://127.0.0.1:$BridgePort/health"
Write-Host "  2. Send the assistant the VPS IP + token name so Render env can be wired:"
Write-Host "     EXECUTION_MODE=mt5_bridge  MT5_BRIDGE_URL=http://<VPS-IP>:$BridgePort  MT5_BRIDGE_TOKEN=***"
Write-Host "  3. Watchdog log: $here\watchdog.log"
Write-Host "  Uninstall: schtasks /Delete /TN ForexMindBridge /F ; schtasks /Delete /TN ForexMindWatchdog /F ; netsh advfirewall firewall delete rule name=""ForexMind Bridge"""
