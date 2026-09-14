@echo off
REM ForexMind MT5 PC connector - edit PAIRING_CODE first (Settings -> Order execution)
if "%PAIRING_CODE%"=="" set PAIRING_CODE=FXM-XXXX-XXXX
set CLOUD_URL=https://forexmind-ai-api.onrender.com
python connector.py
pause
