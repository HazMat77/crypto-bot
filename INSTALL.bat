@echo off
title HazMat Crypto Bot - Full Setup
color 0B
echo.
echo  =============================================
echo   HazMat Crypto Bot - Full Automatic Setup
echo  =============================================
echo.
echo  This will install everything needed to run the bot.
echo  Please keep this window open until complete.
echo.
pause

:: ── Check for internet connection ─────────────────────────────────────────────
ping -n 1 google.com >nul 2>&1
if errorlevel 1 (
    color 0C
    echo  ERROR: No internet connection detected.
    echo  Please connect to the internet and try again.
    echo.
    pause
    exit /b 1
)

:: ── Detect Python (handles both standard and Microsoft Store installs) ─────────
echo.
echo  Detecting Python installation...

:: Try standard python command first
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python
    goto FOUND_PYTHON
)

:: Try py launcher
py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py
    goto FOUND_PYTHON
)

:: Try Microsoft Store Python paths directly
for /d %%D in ("%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.*") do (
    if exist "%%D\LocalCache\local-packages\Python*\Scripts\python.exe" (
        for /f "delims=" %%P in ('dir /b /ad "%%D\LocalCache\local-packages\Python*"') do (
            set PYTHON_CMD="%%D\LocalCache\local-packages\%%P\Scripts\python.exe"
        )
        goto FOUND_PYTHON
    )
)

:: Try common standard install paths
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set PYTHON_CMD="%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto FOUND_PYTHON
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set PYTHON_CMD="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto FOUND_PYTHON
)
if exist "%LOCALAPPDATA%\Programs\Python\Python39\python.exe" (
    set PYTHON_CMD="%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
    goto FOUND_PYTHON
)

:: Python not found anywhere — download it
echo  Python not found. Downloading Python 3.11...
echo.
set PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
set PYTHON_INSTALLER=%TEMP%\python_installer.exe

powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('%PYTHON_URL%', '%PYTHON_INSTALLER%') }"

if not exist "%PYTHON_INSTALLER%" (
    color 0C
    echo  ERROR: Failed to download Python.
    echo  Please download manually from: https://www.python.org/downloads/
    echo  IMPORTANT: During install, check "Add Python to PATH"
    echo  Then re-run this script.
    echo.
    pause
    exit /b 1
)

echo  Installing Python 3.11...
"%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
set "PATH=%LOCALAPPDATA%\Programs\Python\Python311\;%LOCALAPPDATA%\Programs\Python\Python311\Scripts\;%PATH%"
set PYTHON_CMD=python

python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo  Python install failed. Please restart the script or install manually.
    pause
    exit /b 1
)

:FOUND_PYTHON
echo  Python found: 
%PYTHON_CMD% --version
echo.

:: ── Find pip — works for all install types including MS Store ─────────────────
echo  Locating pip...

:: Try getting pip path directly from Python (most reliable method)
for /f "delims=" %%P in ('%PYTHON_CMD% -c "import sys; print(sys.executable)"') do set PYTHON_EXE=%%P
for /f "delims=" %%P in ('%PYTHON_CMD% -c "import sysconfig; print(sysconfig.get_path(\"scripts\"))"') do set SCRIPTS_DIR=%%P

echo  Python executable : %PYTHON_EXE%
echo  Scripts directory : %SCRIPTS_DIR%
echo.

:: Add the scripts dir to PATH so pip works
set "PATH=%SCRIPTS_DIR%;%PATH%"

:: ── Install dependencies using python -m pip (bypasses PATH issues entirely) ──
:: Installed straight from requirements.txt (core + optional) so any package
:: this project adds in the future is picked up automatically — no need to
:: keep this script's package list in sync by hand.
echo  Step: Installing bot dependencies from requirements.txt...
echo  This can take a few minutes on first run — please wait, do not
echo  close this window even if it looks idle for a while.
echo.

%PYTHON_CMD% -m pip install --upgrade pip --no-warn-script-location
%PYTHON_CMD% -m pip install -r requirements.txt --upgrade --no-warn-script-location
echo  (optional packages installed where available)

:: Verify packages installed correctly
%PYTHON_CMD% -c "import kucoin; import pandas; import requests; import websocket" >nul 2>&1
if errorlevel 1 (
    color 0C
    echo.
    echo  ERROR: Package installation failed.
    echo  Try right-clicking INSTALL.bat and choosing "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo  All dependencies installed successfully!
echo.

:: ── Save the working python command for START_BOT.bat to use ──────────────────
echo %PYTHON_EXE% > python_path.txt

:: ── Optional Windows startup ──────────────────────────────────────────────────
echo  -- Optional: Run bot on Windows startup --------------------
echo.
echo  Would you like the bot launcher to open automatically when Windows starts?
echo.
set /p STARTUP="  Add to startup? (Y/N): "

set "STARTUP_LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\HazMat Crypto Bot.bat"

if /i "%STARTUP%"=="Y" (
    call :WRITE_STARTUP_LINK
    if exist "%STARTUP_LINK%" (
        echo  Startup entry added!
    ) else (
        echo  Could not add startup entry automatically.
    )
) else (
    echo  Skipped.
)
goto AFTER_STARTUP

:WRITE_STARTUP_LINK
echo @echo off > "%STARTUP_LINK%"
echo cd /d "%~dp0" >> "%STARTUP_LINK%"
echo call "%~dp0START_BOT.bat" >> "%STARTUP_LINK%"
exit /b 0

:AFTER_STARTUP

:: ── Optional: create a desktop shortcut with the HazMat Crypto Bot icon ─────────
echo.
echo  -- Optional: Create a desktop shortcut with the HazMat Crypto Bot icon --
echo.
set /p MAKESHORTCUT="  Create desktop shortcut? (Y/N): "

if /i "%MAKESHORTCUT%"=="Y" (
    if exist "%~dp0icon.ico" (
        powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $desktop = $ws.SpecialFolders('Desktop'); $lnk = $ws.CreateShortcut(\"$desktop\HazMat Crypto Bot.lnk\"); $lnk.TargetPath = '%~dp0START_BOT.bat'; $lnk.WorkingDirectory = '%~dp0'; $lnk.IconLocation = '%~dp0icon.ico'; $lnk.Description = 'HazMat Crypto Bot'; $lnk.Save()"
        powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $desktop = $ws.SpecialFolders('Desktop'); $lnk = $ws.CreateShortcut(\"$desktop\HazMat Crypto Bot Watchdog.lnk\"); $lnk.TargetPath = '%~dp0WATCHDOG.bat'; $lnk.WorkingDirectory = '%~dp0'; $lnk.IconLocation = '%~dp0icon.ico'; $lnk.Description = 'HazMat Crypto Bot Watchdog'; $lnk.Save()"
        powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $desktop = $ws.SpecialFolders('Desktop'); if (Test-Path \"$desktop\HazMat Crypto Bot.lnk\") { exit 0 } else { exit 1 }"
        if errorlevel 1 (
            echo  Could not create the shortcut automatically - you can still
            echo  right-click START_BOT.bat -^> Send to -^> Desktop, then
            echo  right-click the new shortcut -^> Properties -^> Change Icon
            echo  and point it at icon.ico in this folder.
        ) else (
            echo  Desktop shortcuts created with custom icon!
        )
    ) else (
        echo  icon.ico not found in this folder - skipping shortcut icon.
        echo  (You can still create a normal shortcut to START_BOT.bat by hand.)
    )
) else (
    echo  Skipped.
)

:: ── Optional: reduce auto-restart risk for continuous trading ─────────────────
echo.
echo  -- Optional: Reduce auto-restart risk while the bot is running --
echo.
echo  This does NOT disable Windows Update or security patches. It only
echo  changes WHEN Windows is allowed to restart automatically:
echo    1. Sets Active Hours to 6:00 AM - 11:00 PM (the max allowed span,
echo       18 hours) so Windows won't auto-restart during that window.
echo    2. Blocks auto-restart entirely while you're logged in, any time
echo       of day - this is the stronger of the two settings.
echo  Outside active hours, with nobody logged in, Windows can still
echo  restart to finish installing updates - that's by design, so your
echo  security patches still actually get applied.
echo  Requires running this installer as Administrator. Skips safely
echo  with a message below if it isn't.
echo.
set /p NORESTART="  Apply this? (Y/N): "

if /i "%NORESTART%"=="Y" (
    net session >nul 2>&1
    if errorlevel 1 (
        echo  Skipped - this installer isn't running as Administrator.
        echo  Right-click INSTALL.bat and choose "Run as administrator"
        echo  to apply this setting.
    ) else (
        reg add "HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" /v "ActiveHoursStart" /t REG_DWORD /d 6  /f >nul 2>&1
        reg add "HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" /v "ActiveHoursEnd"   /t REG_DWORD /d 23 /f >nul 2>&1
        reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v "NoAutoRebootWithLoggedOnUsers" /t REG_DWORD /d 1 /f >nul 2>&1
        if errorlevel 1 (
            echo  Could not apply the setting - you can set it manually under
            echo  Settings -^> Windows Update -^> Advanced options -^> Active hours.
        ) else (
            echo  Done. Active hours set to 6 AM - 11 PM, and auto-restart is
            echo  blocked while you're logged in. Windows Update itself is
            echo  untouched - patches still download and install normally.
        )
    )
) else (
    echo  Skipped.
)

:: ── Done ──────────────────────────────────────────────────────────────────────
color 0A
echo.
echo  =============================================
echo   Setup complete! Everything is ready.
echo  =============================================
echo.
echo  NEXT STEPS:
echo    1. Open config.py in Notepad
echo    2. Fill in your KuCoin API Passphrase
echo    3. Fill in your Telegram token + chat ID (optional)
echo    4. BACKTEST first: python backtest.py --symbol BTC-USDT --days 90
echo    5. Double-click START_BOT.bat to launch the bot
echo      (or use the HazMat Crypto Bot desktop shortcut, if you created one)
echo.
echo  BACKTEST EXAMPLES:
echo    python backtest.py --symbol BTC-USDT --days 90
echo    python backtest.py --symbol ETH-USDT --days 180 --rsi-buy 35 --rsi-sell 65
echo    python backtest.py --all-coins --days 60 --plot
echo.
echo  Remember to regenerate your KuCoin API keys after testing!
echo.
pause
