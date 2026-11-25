@echo off
REM jianying-draft-mcp - stdio launcher
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found. Install Python 3.10+ and add it to PATH. 1>&2
  exit /b 1
)
python "server\jianying_mcp_server.py" 2>>"mcp.log"
