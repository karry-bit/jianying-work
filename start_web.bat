@echo off
cd /d %~dp0
start "" http://127.0.0.1:8765
py -3.12 web\server.py --port 8765
pause
