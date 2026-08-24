@echo off
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto :launch

echo First-time setup: creating a virtual environment and installing dependencies...
echo (This only happens once - it may take a minute.)
echo.

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)

if not exist ".venv\Scripts\pythonw.exe" (
    echo.
    echo Could not create the virtual environment.
    echo Make sure Python 3.11+ is installed and available as "python" or via the "py" launcher.
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install dependencies. See the error above.
    pause
    exit /b 1
)

echo.
echo Setup complete.

:launch
start "" ".venv\Scripts\pythonw.exe" "launch_app.py"
