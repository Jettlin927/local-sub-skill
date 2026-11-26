@echo off
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%scripts"

where python >nul 2>&1
if %errorlevel%==0 (
  set "PY=python"
) else (
  where py >nul 2>&1
  if %errorlevel%==0 (
    set "PY=py"
  ) else (
    echo [ERROR] Python 3.10+ not found. Please install Python from https://www.python.org/
    pause
    exit /b 1
  )
)

if "%~1"=="" (
  echo Launching local-sub-skill Drag-and-Drop Studio...
  "%PY%" "%ROOT%scripts\gui.py"
  exit /b %errorlevel%
)

set "TARGET=%~1"
if exist "%TARGET%\" (
  echo [Batch Folder] %TARGET%
  "%PY%" "%ROOT%scripts\run_batch.py" "%TARGET%" --language auto --bilingual --ass --continue-on-error
) else if /i "%~x1"==".srt" (
  echo [Translate Subtitle] %TARGET%
  "%PY%" "%ROOT%scripts\local_translation.py" "%TARGET%" --source auto --target zh --bilingual --ass
) else if /i "%~x1"==".vtt" (
  echo [Translate WebVTT] %TARGET%
  "%PY%" "%ROOT%scripts\local_translation.py" "%TARGET%" --source auto --target zh --bilingual --ass
) else (
  echo [Video -> Chinese + Bilingual SRT/ASS] %TARGET%
  "%PY%" "%ROOT%scripts\run_single_video.py" "%TARGET%" --language auto --bilingual --ass
)

echo.
echo Done. Check the output .zh.srt / .bilingual.srt / .bilingual.ass next to your input file.
pause
