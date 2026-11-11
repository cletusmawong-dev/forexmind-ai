# Install ForexMind bridge slots 2-5. Does not stop, restart, or reconfigure
# the existing bridge on port 8700.
#
# Run in an elevated PowerShell on the Windows VPS, from the repo root:
#   powershell -ExecutionPolicy Bypass -File vps\install-extra-slots.ps1
#
# Then edit C:\ForexMind\secrets\accounts\N.env and follow
# docs\MULTI_ACCOUNT_SETUP.md to copy a portable terminal per slot.

$ErrorActionPreference = "Stop"

$Root = "C:\ForexMind"
$SecretDir = Join-Path $Root "secrets\accounts"
$TermRoot = Join-Path $Root "terminals"
$RepoBridge = Join-Path (Split-Path $PSScriptRoot -Parent) "vps-bridge\bridge.py"
if (-not (Test-Path $RepoBridge)) {
    $RepoBridge = "C:\ForexMind\repo\vps-bridge\bridge.py"
}

New-Item -ItemType Directory -Force -Path $SecretDir | Out-Null
New-Item -ItemType Directory -Force -Path $TermRoot | Out-Null

# One shared token with slot 1. The cloud already has MT5_BRIDGE_TOKEN.
# Slots 2-5 send the same X-Bridge-Token header. They do not listen on
# 0.0.0.0 (see BRIDGE_HOST below).
$Token = $env:BRIDGE_TOKEN
if (-not $Token) {
    $Token = Read-Host "Paste the existing BRIDGE_TOKEN (same value as slot 1)"
}
if (-not $Token) { throw "BRIDGE_TOKEN is required. Slots are never started without it." }

$Ports = @{ 2 = 8701; 3 = 8702; 4 = 8703; 5 = 8704 }

foreach ($slot in 2..5) {
    $port = $Ports[$slot]
    $envFile = Join-Path $SecretDir "$slot.env"
    $termDir = Join-Path $TermRoot "slot$slot"
    New-Item -ItemType Directory -Force -Path $termDir | Out-Null
    if (-not (Test-Path $envFile)) {
        @"
BRIDGE_TOKEN=$Token
BRIDGE_HOST=127.0.0.1
BRIDGE_PORT=$port
BRIDGE_SLOT=$slot
MT5_TERMINAL_PATH=$termDir\terminal64.exe
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
"@ | Set-Content -Path $envFile -Encoding ASCII
        Write-Host "Wrote $envFile"
    } else {
        Write-Host "Left existing $envFile alone"
    }

    $start = Join-Path $Root "start-slot$slot.cmd"
    @"
@echo off
REM Slot $slot only. Does not touch port 8700.
setlocal
for /f "usebackq tokens=1,* delims==" %%A in ("$envFile") do (
  if not "%%A"=="" set %%A=%%B
)
python "$RepoBridge"
"@ | Set-Content -Path $start -Encoding ASCII

    $task = "ForexMindBridgeSlot$slot"
    $arg = "/c `"$start`""
    schtasks /Create /F /TN $task /SC ONSTART /RL HIGHEST /TR "cmd.exe $arg" | Out-Null
    Write-Host "Scheduled task $task -> $start (not started yet)"
}

Write-Host ""
Write-Host "Slot 1 on port 8700 was not modified and was not restarted."
Write-Host "Fill MT5_LOGIN, MT5_PASSWORD, MT5_SERVER in each env file,"
Write-Host "copy a portable MT5 into C:\ForexMind\terminals\slotN,"
Write-Host "then start one slot with:  schtasks /Run /TN ForexMindBridgeSlot2"
Write-Host "See docs\MULTI_ACCOUNT_SETUP.md"
