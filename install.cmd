@echo off
rem Install ca on Windows (double-click). Open a new terminal afterwards.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ca-win.ps1" install
pause
