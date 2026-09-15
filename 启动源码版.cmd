@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.13 -m venv .venv
  if errorlevel 1 (
    echo 请先安装 Python 3.13。
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 依赖安装失败，请检查网络。
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" portable_app.py
if errorlevel 1 pause
