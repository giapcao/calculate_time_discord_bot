@echo off
setlocal

cd /d "%~dp0"

if not exist ".env" (
  echo [ERROR] .env not found.
  echo Copy .env.example to .env and set DISCORD_TOKEN first.
  echo Example:
  echo   copy .env.example .env
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creating virtual environment...
  py -m venv .venv || python -m venv .venv
)

echo [INFO] Installing/updating dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo [INFO] Starting Discord bot...
".venv\Scripts\python.exe" bot.py

endlocal
