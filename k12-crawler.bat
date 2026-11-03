@echo off
setlocal
if not exist "%~dp0.venv\Scripts\k12-crawler.exe" (
  echo Run install.bat first.
  pause
  exit /b 1
)
if "%~1"=="" (
  "%~dp0.venv\Scripts\k12-crawler.exe" --help
  pause
  exit /b 0
)
"%~dp0.venv\Scripts\k12-crawler.exe" %*
