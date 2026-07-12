#!/bin/bash
cd "$(dirname "$0")/backend"

if ! command -v python3 &> /dev/null; then
    echo "Python 3 не найден. Установите Python 3.10+ и запустите скрипт снова."
    read -p "Нажмите Enter для выхода..."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Первый запуск: настраиваю окружение..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip > /dev/null
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

python3 app.py
