@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3.9 or newer was not found.
  echo Install it from https://www.python.org/downloads/windows/
  echo and tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Could not create .venv
    pause
    exit /b 1
  )
)

echo [2/3] Installing dependencies ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
  echo [ERROR] Installation failed. Check your network or proxy settings.
  pause
  exit /b 1
)

echo [3/3] Done.
echo.
echo Run:  k12-crawler.bat --help
echo Try:  k12-crawler.bat sites --json
pause
