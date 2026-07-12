@echo off
chcp 65001 >nul
title Downlink
cd /d "%~dp0backend"

where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден. Установите Python 3.10+ с сайта python.org
    echo и поставьте галочку "Add Python to PATH" при установке.
    pause
    exit /b
)

if not exist ".venv" (
    echo Первый запуск: настраиваю окружение, это займёт минуту...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip >nul
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

python app.py
pause
