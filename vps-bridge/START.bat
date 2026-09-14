@echo off
REM ForexMind MT5 bridge launcher - edit your token first!
if "%BRIDGE_TOKEN%"=="" set BRIDGE_TOKEN=paste-a-long-random-secret-here
python bridge.py
pause
