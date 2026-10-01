@echo off
setlocal
set "DIR=%USERPROFILE%\QuestionTemplateHelper"
if not exist "%DIR%" mkdir "%DIR%"
echo Updating shared helper code. Existing .venv and Torch are preserved.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $dest=Join-Path $env:USERPROFILE 'QuestionTemplateHelper'; $base='https://raw.githubusercontent.com/EvanR12345/questiontemplate.com/main/nvidia-helper/'; $names=@('server.py','image_engine.py','image_queue.py','requirements.txt','start-windows.bat'); foreach($name in $names){Invoke-WebRequest -UseBasicParsing ($base+$name) -OutFile (Join-Path $dest ($name+'.new'))}; foreach($name in $names){$path=Join-Path $dest $name; if(Test-Path -LiteralPath $path){Copy-Item -LiteralPath $path -Destination ($path+'.previous') -Force}; Move-Item -LiteralPath ($path+'.new') -Destination $path -Force}"
if errorlevel 1 (
  echo Update failed. Installed code and environment are preserved.
  pause
  exit /b 1
)
call "%DIR%\start-windows.bat"
