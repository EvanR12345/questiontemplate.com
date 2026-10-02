@echo off
setlocal
cd /d "%~dp0"
echo QuestionTemplate - shared NVIDIA helper (Audio + Studio)
rem Updates are explicit; never overwrite installed fixes during launch.
if not exist ".venv\Scripts\python.exe" (
  echo Shared .venv is missing. This launcher will not create or replace it.
  pause
  exit /b 1
)
rem Reopen the paired public Studio when the shared helper is already running.
.venv\Scripts\python.exe -c "import pathlib,urllib.request,webbrowser; key=pathlib.Path('.pairing-key').read_text().strip(); req=urllib.request.Request('http://127.0.0.1:8765/health',headers={'Authorization':'Bearer '+key}); urllib.request.urlopen(req,timeout=2).read(); webbrowser.open('https://questiontemplate.com/manga.html#native='+key)" >nul 2>nul
if not errorlevel 1 exit /b
.venv\Scripts\python.exe -c "import torch; assert torch.cuda.is_available(), 'Check the NVIDIA driver'"
if errorlevel 1 goto failed
.venv\Scripts\python.exe -c "import kokoro,diffusers,accelerate,safetensors,PIL"
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
rem English pronunciation data only; never resolve another Torch installation.
.venv\Scripts\python.exe -c "import en_core_web_sm" >nul 2>nul
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install --no-deps "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe server.py
if errorlevel 1 goto failed
exit /b
:failed
echo Helper failed. Read the message above. The existing environment was preserved.
pause
exit /b 1
