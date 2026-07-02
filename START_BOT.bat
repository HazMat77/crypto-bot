@echo off
title CryptoTradingBot
color 0A
echo.
echo  =============================================
echo   CryptoTradingBot - RSI + MA Trading Bot
echo  =============================================
echo.

:: ── Find Python — use saved path from install, or auto-detect ─────────────────
set PYTHON_CMD=

:: Check if INSTALL.bat saved the python path
if exist "%~dp0python_path.txt" (
    set /p PYTHON_CMD=<"%~dp0python_path.txt"
    :: Trim whitespace/newline
    for /f "tokens=* delims= " %%A in ("%PYTHON_CMD%") do set PYTHON_CMD=%%A
)

:: Fallback: try standard commands
if "%PYTHON_CMD%"=="" (
    python --version >nul 2>&1
    if not errorlevel 1 set PYTHON_CMD=python
)
if "%PYTHON_CMD%"=="" (
    py --version >nul 2>&1
    if not errorlevel 1 set PYTHON_CMD=py
)

:: Still not found
if "%PYTHON_CMD%"=="" (
    color 0C
    echo  ERROR: Python not found.
    echo  Please run INSTALL.bat first.
    echo.
    pause
    exit /b 1
)

:: ── Check dependencies ─────────────────────────────────────────────────────────
"%PYTHON_CMD%" -c "import kucoin, pandas, numpy, requests, bs4, websocket" >nul 2>&1
if errorlevel 1 (
    color 0E
    echo  Dependencies not found. Installing now from requirements.txt...
    echo.
    "%PYTHON_CMD%" -m pip install -r requirements.txt --quiet --no-warn-script-location
    echo.
)

:: ── Mode selection menu ───────────────────────────────────────────────────────
:MENU
echo  Which mode would you like to run?
echo.
echo    [1]  Paper trading      (simulation - no real money)
echo    [2]  Live trading       (uses real money on KuCoin)
echo    [3]  GUI Dashboard      (desktop monitoring window)
echo    [4]  Web Dashboard      (browser-based Streamlit)
echo    [5]  Backtest           (test strategy on historical data)
echo    [6]  Exit
echo.
set /p CHOICE="  Enter 1-6: "

if "%CHOICE%"=="1" goto PAPER
if "%CHOICE%"=="2" goto LIVE
if "%CHOICE%"=="3" goto GUI
if "%CHOICE%"=="4" goto DASHBOARD
if "%CHOICE%"=="5" goto BACKTEST
if "%CHOICE%"=="6" goto END
echo.
echo  Invalid choice.
echo.
goto MENU

:GUI
color 0B
echo.
echo  Opening GUI Dashboard...
echo  (Close the window to return here)
echo.
cd /d "%~dp0"
"%PYTHON_CMD%" gui_dashboard.py
goto DONE

:DASHBOARD
color 0B
echo.
echo  Opening dashboard in your browser...
echo  Press CTRL+C to stop the dashboard server.
echo.
cd /d "%~dp0"
"%PYTHON_CMD%" -m streamlit run dashboard.py
goto DONE

:BACKTEST
color 0A
echo.
echo  Backtest options:
echo    [1]  Quick backtest (BTC, 90 days)
echo    [2]  All coins backtest (90 days)
echo    [3]  Full optimize + Monte Carlo (BTC, 180 days)
echo    [4]  Custom (opens command prompt)
echo.
set /p BTCHOICE="  Enter 1-4: "
cd /d "%~dp0"
if "%BTCHOICE%"=="1" "%PYTHON_CMD%" backtest.py --symbol BTC-USDT --days 90
if "%BTCHOICE%"=="2" "%PYTHON_CMD%" backtest.py --all-coins --days 90
if "%BTCHOICE%"=="3" "%PYTHON_CMD%" backtest.py --symbol BTC-USDT --optimize --monte-carlo --days 180
if "%BTCHOICE%"=="4" cmd /k
goto DONE

:PAPER
color 0A
echo.
echo  Starting in PAPER TRADING mode (simulation only)...
echo  No real money will be used.
echo  Logs are saved in the "logs" folder.
echo.
call :LAUNCH_WATCHDOG
echo  Press CTRL+C to stop.
echo.
cd /d "%~dp0"
"%PYTHON_CMD%" bot.py --mode paper
goto DONE

:LIVE
color 0E
echo.
echo  !! WARNING: LIVE TRADING MODE !!
echo  Real money will be used on your KuCoin account.
echo  Make sure your API keys are set in config.py
echo.
set /p CONFIRM="  Type YES to confirm and start live trading: "
if /i not "%CONFIRM%"=="YES" (
    echo.
    echo  Cancelled. Returning to menu...
    echo.
    goto MENU
)
color 0C
echo.
echo  Starting LIVE trading...
echo  Logs are saved in the "logs" folder.
echo.
call :LAUNCH_WATCHDOG
echo  Press CTRL+C to stop.
echo.
cd /d "%~dp0"
"%PYTHON_CMD%" bot.py --mode live
goto DONE

:LAUNCH_WATCHDOG
:: ── Auto-start the watchdog in its own window alongside the bot ───────────
:: This is what makes "set and forget" actually work — without this, the
:: watchdog only runs if you remembered to open WATCHDOG.bat yourself in a
:: second window. Skips launching a duplicate if one is already running.
tasklist /fi "windowtitle eq CryptoTradingBot Watchdog*" 2>nul | find /i "cmd.exe" >nul
if errorlevel 1 (
    echo  Launching watchdog in a separate window...
    start "CryptoTradingBot Watchdog" cmd /c "cd /d "%~dp0" && WATCHDOG.bat"
) else (
    echo  Watchdog already running in another window - not starting a duplicate.
)
exit /b 0

:DONE
echo.
echo  Bot has stopped.
pause
goto END

:END
