#!/usr/bin/env bash
# cron_wrapper.sh — обёртка для автоматического запуска harvest по расписанию (Linux/WSL)
#
# Использование:
#   1. Разместить этот файл в корне проекта (рядом с cyborg/)
#   2. Сделать исполняемым: chmod +x cron_wrapper.sh
#   3. Добавить в crontab: crontab -e
#      Пример (каждые 30 минут):
#      */30 * * * * /path/to/kiborg/deployment/cron_wrapper.sh
#   4. Логи падают в cyborg/data/cron_<YYYY-MM-DD_HHMMSS>.log

set -euo pipefail

# === КОНФИГурация — замени под свою машину ===
# Путь к проекту (текущая директория по дефолту)
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Python интерпретатор (если проект в venv — активируем его)
PYTHON="${PYTHON:-python3}"
if [[ -n "${KIBORG_VENV:-}" ]]; then
    # shellcheck source=/dev/null
    source "$KIBORG_VENV/bin/activate"
fi

# Аргументы для harvest.py (по умолчанию 1 прогон)
HARVEST_ARGS="${HARVEST_ARGS:-1}"

# === Логирование ===
LOG_DIR="$PROJECT_ROOT/cyborg/data"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/cron_$(date +%Y-%m-%d_%H%M%S).log"

echo "[$(date)] Starting harvest wrapper: PROJECT_ROOT=$PROJECT_ROOT, PYTHON=$PYTHON, HARVEST_ARGS=$HARVEST_ARGS" | tee -a "$LOG_FILE"

# === Запуск ===
cd "$PROJECT_ROOT" || exit 1
if ! $PYTHON cyborg/harvest.py $HARVEST_ARGS 2>&1 | tee -a "$LOG_FILE"; then
    echo "[$(date)] Harvest failed with exit code $?" | tee -a "$LOG_FILE"
    exit 1
fi

echo "[$(date)] Harvest completed successfully" | tee -a "$LOG_FILE"
