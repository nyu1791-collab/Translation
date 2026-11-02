@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  echo Prepare this repository with uv sync --locked first.
  pause
  exit /b 1
)
if "%~1"=="" (
  .venv\Scripts\python.exe -m translation --help
  pause
) else (
  .venv\Scripts\python.exe -m translation %*
)
exit /b %errorlevel%
