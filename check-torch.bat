@echo off
setlocal
title QuestionTemplate - Check Torch installs
echo ==============================================
echo   QuestionTemplate - Torch duplicate checker
echo ==============================================
echo.
echo Scanning common helper locations. Nothing will be deleted.
echo This can take a minute if your Downloads folder is large.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "$roots=@();" ^
  "$candidates=@($env:USERPROFILE+'\QuestionTemplateHelper',$env:USERPROFILE+'\Downloads',$env:USERPROFILE+'\Desktop',$env:USERPROFILE+'\Documents',$env:USERPROFILE+'\OneDrive');" ^
  "foreach($r in $candidates){if(Test-Path $r){$roots += $r}};" ^
  "$py=@();" ^
  "foreach($r in $roots){$py += Get-ChildItem -Path $r -Filter python.exe -File -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match '[\\/](\.venv|venv)[\\/]Scripts[\\/]python\.exe$' }};" ^
  "$py=$py | Sort-Object FullName -Unique;" ^
  "$found=@();" ^
  "foreach($p in $py){" ^
    "$code='import json,os; import torch; print(json.dumps({"version":torch.__version__,"cuda":torch.version.cuda,"cuda_ok":torch.cuda.is_available(),"path":os.path.dirname(torch.__file__)}))';" ^
    "$raw=& $p.FullName -c $code 2>$null;" ^
    "if($LASTEXITCODE -eq 0 -and $raw){" ^
      "try{$j=$raw|ConvertFrom-Json}catch{continue};" ^
      "$bytes=(Get-ChildItem -LiteralPath $j.path -File -Recurse -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum;" ^
      "$found += [pscustomobject]@{Python=$p.FullName;Torch=$j.version;CUDA=$j.cuda;CUDA_OK=$j.cuda_ok;TorchPath=$j.path;TorchFolderGB=[math]::Round(($bytes/1GB),2)}" ^
    "}" ^
  "};" ^
  "if($found.Count -eq 0){Write-Host 'No Torch installs were found in the common helper folders.' -ForegroundColor Yellow}" ^
  "else{$found | Format-List; Write-Host ('Torch environments found: '+$found.Count) -ForegroundColor Cyan;" ^
    "if($found.Count -gt 1){Write-Host 'More than one Torch environment exists. This does not automatically mean both are needed.' -ForegroundColor Yellow}" ^
    "else{Write-Host 'Only one Torch environment was found in the scanned folders.' -ForegroundColor Green}" ^
  "};" ^
  "$cache=Join-Path $env:LOCALAPPDATA 'pip\Cache';" ^
  "if(Test-Path $cache){$torchCache=Get-ChildItem $cache -File -Recurse -ErrorAction SilentlyContinue | Where-Object {$_.Name -match '^(torch|nvidia).*\.(whl|body)$'}; $sum=($torchCache|Measure-Object Length -Sum).Sum; if($sum -gt 0){Write-Host ('Possible Torch/NVIDIA pip cache: '+[math]::Round($sum/1GB,2)+' GB at '+$cache) -ForegroundColor DarkYellow}}"
echo.
echo Finished. You can send me a screenshot of the results and I can tell you which copy is the old helper and which is the new one.
echo.
pause
