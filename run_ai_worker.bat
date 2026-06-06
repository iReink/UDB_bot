@echo off
setlocal

cd /d "%~dp0"

if exist "ai_worker.local.bat" (
    call "ai_worker.local.bat"
)

if "%AI_WORKER_BACKEND_URL%"=="" set "AI_WORKER_BACKEND_URL=http://94.183.184.65:8080"
if "%OLLAMA_URL%"=="" set "OLLAMA_URL=http://localhost:11434"
if "%AI_WORKER_POLL_SECONDS%"=="" set "AI_WORKER_POLL_SECONDS=5"

if "%AI_WORKER_TOKEN%"=="" (
    echo AI_WORKER_TOKEN is not set. Create ai_worker.local.bat from ai_worker.local.example.bat.
    pause
    exit /b 2
)

py -3 "%~dp0ai_worker.py"
if errorlevel 1 pause
