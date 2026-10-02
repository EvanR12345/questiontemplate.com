@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Existing shared environment is missing. This script will not replace it.
  pause
  exit /b 1
)
echo Optional local director and image models. Approximately 7.7 GB if not already installed.
echo Configured existing model files are reused; no Torch or CUDA installation.
.venv\Scripts\python.exe install-studio-models.py %*
if errorlevel 1 echo Model setup failed; read the error above. Existing environment was retained.
pause
