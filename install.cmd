@echo off
rem Install cx on Windows. Open a new terminal afterwards.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cx.ps1" install
pause
