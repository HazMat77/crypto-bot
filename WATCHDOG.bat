@echo off
title CryptoTradingBot Watchdog
color 0E
echo.
echo  =============================================
echo   CryptoTradingBot Watchdog - Health Monitor
echo  =============================================
echo.
echo  This runs SEPARATELY from the bot itself.
echo  Keep this window open alongside the bot window.
echo.
echo  It checks every 60 seconds whether the bot is
echo  still alive and responsive, and alerts you on
echo  Telegram (independently of the bot's own alerts)
echo  if something has frozen or crashed.
echo.

set PYTHON_CMD=
if exist "%~dp0python_path.txt" (
    set /p PYTHON_CMD=<"%~dp0python_path.txt"
    for /f "tokens=* delims= " %%A in ("%PYTHON_CMD%") do set PYTHON_CMD=%%A
)
if "%PYTHON_CMD%"=="" (
    python --version >nul 2>&1
    if not errorlevel 1 set PYTHON_CMD=python
)
if "%PYTHON_CMD%"=="" (
    py --version >nul 2>&1
    if not errorlevel 1 set PYTHON_CMD=py
)
if "%PYTHON_CMD%"=="" (
    echo  ERROR: Python not found. Run INSTALL.bat first.
    pause
    exit /b 1
)

cd /d "%~dp0"
"%PYTHON_CMD%" watchdog.py

pause
