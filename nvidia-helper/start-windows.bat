@echo off
setlocal
cd /d "%~dp0"
echo QuestionTemplate - local NVIDIA helper (Audio + Studio)

rem Pull the latest helper code on every launch. If offline, keep using the installed copy.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $base='https://raw.githubusercontent.com/EvanR12345/questiontemplate.com/main/nvidia-helper/'; Invoke-WebRequest -UseBasicParsing ($base+'server.py') -OutFile 'server.py.new'; Invoke-WebRequest -UseBasicParsing ($base+'requirements.txt') -OutFile 'requirements.txt.new'; Move-Item -Force 'server.py.new' 'server.py'; Move-Item -Force 'requirements.txt.new' 'requirements.txt'" >nul 2>nul
if errorlevel 1 echo Could not check for updates. Starting the installed helper.

if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv
  if errorlevel 1 goto python_missing
)
if not exist ".venv\installed-v2.txt" (
  echo Updating the shared helper. First image generation downloads additional model files.
  .venv\Scripts\python.exe -m pip install --upgrade pip
  if errorlevel 1 goto failed
  .venv\Scripts\python.exe -c "import torch; assert torch.cuda.is_available()" >nul 2>nul
  if errorlevel 1 (
    .venv\Scripts\python.exe -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
    if errorlevel 1 goto failed
  )
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto failed
  echo ready> .venv\installed-v2.txt
)
.venv\Scripts\python.exe server.py
if errorlevel 1 goto failed
goto end
:python_missing
echo Install Python 3.11 for Windows with the Python launcher enabled:
echo https://www.python.org/downloads/release/python-3119/
echo Then run this file again.
goto end
:failed
echo Setup or engine failed. Read the message above. Check your NVIDIA driver and internet connection.
:end
pause
