@echo off
set "XIAOA_UNINSTALL_SCRIPT=%~dp0uninstall.ps1"
cd /d "%TEMP%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%XIAOA_UNINSTALL_SCRIPT%"
if errorlevel 1 pause
