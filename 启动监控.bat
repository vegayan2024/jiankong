@echo off
title QUANT RADAR - jiankong
echo ========================================================
echo   QUANT RADAR (jiankong)
echo   Local URL: http://127.0.0.1:8765
echo ========================================================
start "" "http://127.0.0.1:8765"
cd /d "%~dp0"
python scripts/web_server.py
pause