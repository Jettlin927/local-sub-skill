@echo off
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%scripts"

where python >nul 2>&1
if %errorlevel%==0 (
  python "%ROOT%scripts\gui.py"
) else (
  py "%ROOT%scripts\gui.py"
)
pause
