@echo off
title TCFL Attendance System - Stop
echo Stopping the TCFL Attendance System...
taskkill /FI "WINDOWTITLE eq TCFL Attendance Server*" /T /F >nul 2>&1
echo Done. You can close this window.
echo.
pause
