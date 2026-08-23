@echo off
title TCFL Attendance System - Launcher
cd /d "%~dp0"

echo Starting the TCFL Attendance System...
echo.

if not exist venv (
    echo ERROR: venv folder not found. Run the one-time setup first:
    echo    py -m venv venv
    echo    venv\Scripts\activate
    echo    pip install -r requirements.txt
    echo.
    pause
    exit /b
)

call venv\Scripts\activate.bat

start "TCFL Attendance Server" /min cmd /c "python app.py"

echo Waiting for the server to start...
timeout /t 3 /nobreak >nul

start "" http://localhost:5000

echo.
echo The system is running. A browser window should now be open.
echo.
echo IMPORTANT: Keep this window open while presenting.
echo When you're done, run Stop_Attendance_System.bat
echo (or just close this window and the minimized server window).
echo.
pause
