"""Автоматическое восстановление state.json из бэкапа при повреждении.

Зачем: state.json пишется под state_lock (frozen idea_engine/store.py), но порча всё
равно возможна — крах процесса в середине atomic save (маловероятно, но возможно при
полном диске / OOM-kill / ручной правке с ошибкой). Без авто-восстановления прогон
падает, и админ должен вручную запускать restore_backup.py согласно RUNBOOK.

Поведение: harvest_runner.main() перед прогоном (ПОСЛЕ ensure_data_dirs, ДО backup_state)
зовёт auto_recover_state_if_needed(). Если state.json повреждён/отсутствует И есть
валидный бэкап — восстанавливает, отправляет CRITICAL-алерт, логирует факт. Прогон
продолжается как ни в чём не бывало (только с откатнувшимся состоянием).

Безопасность:
  - Повреждённый файл НЕ затирается: сохраняется как state.json.corrupted-<TS> для
    разбора (что именно сломалось — важно для диагностики).
  - frozen store.py НЕ трогаем — проверка идёт ДО передачи управления органам.
  - Бэкапы НЕ удаляем (ротация остаётся за backup.py).
  - Если бэкапов нет — НЕ создаём пустой state.json (прогон упадёт в Organs с
    прозрачной ошибкой; лучше диагностический крах, чем тихая потеря истории).

Возвращаемое значение — словарь с результатом (для логирования/тестов/алертинга):
  {"recovered": bool, "backup_ts": str|None, "error": str|None}
"""

import datetime
import json
import os
import shutil


def _is_valid_json(path):
    """True если файл существует и json.load проходит. Иначе False."""
    try:
        with open(path, encoding="utf-8") as f:
            json.load(f)
        return True
    except (OSError, ValueError):
        return False


def _find_latest_valid_backup(backups_dir):
    """Найти последнюю (по TS-имени) поддиректорию с валидным state.json.

    Имена подкаталогов имеют формат %Y-%m-%d_%H%M%S (см. backup.py) — лексикографическая
    сортировка = хронологическая, свежие первыми. Возвращает TS-имя или None.
    """
    try:
        names = [d for d in os.listdir(backups_dir) if os.path.isdir(os.path.join(backups_dir, d))]
    except OSError:
        return None
    for name in sorted(names, reverse=True):  # свежие первыми
        candidate = os.path.join(backups_dir, name, "state.json")
        if _is_valid_json(candidate):
            return name
    return None


def auto_recover_state_if_needed(state_path, backups_dir, max_backups=None):
    """Проверить state.json; при повреждении — восстановить из последнего бэкапа.

    Логика:
      1. state_path валиден → no-op, {"recovered": False, "backup_ts": None, "error": None}.
         (нормальный путь — ничего не делаем, прогон идёт как обычно)
      2. state_path повреждён/отсутствует → ищем свежий валидный бэкап:
         - найден → сохраняем текщий (если есть) как .corrupted-<TS>, копируем бэкап,
           возвращаем {"recovered": True, "backup_ts": <TS>, "error": None}.
         - НЕ найден (нет бэкапов / все тоже повреждены) → НЕ трогаем текущий файл,
           возвращаем {"recovered": False, "backup_ts": None, "error": "..."}.
           Если файла state_path не было (fresh install) и бэкапов тоже нет — это
           нормальная стартовая точка, error="no state.json and no backups (fresh install?)".

    Аргумент `max_backups` сейчас НЕ используется (ротация — ответственность backup.py),
    но присутствует в сигнатуре, чтобы вызывающий код (и тесты) явно передавал контекст.
    Запрещено удалять бэкапы отсюда.
    """
    # Нормальный путь: state.json валиден → ничего не делаем.
    if _is_valid_json(state_path):
        return {"recovered": False, "backup_ts": None, "error": None}

    # Повреждён или отсутствует — ищем бэкап.
    backup_ts = _find_latest_valid_backup(backups_dir)
    if backup_ts is None:
        # Нет валидного бэкапа. Различаем два случая для более точного сообщения:
        # файла не было (fresh install) vs он был, но битый.
        if os.path.exists(state_path):
            err = "state.json corrupted and no valid backup found"
        else:
            err = "no state.json and no valid backup (fresh install?)"
        print(f"[recover] {err} — восстановление невозможно, прогон продолжится как есть")
        return {"recovered": False, "backup_ts": None, "error": err}

    src = os.path.join(backups_dir, backup_ts, "state.json")

    # Сохранить повреждённый файл для разбора (если он существует — при отсутствии
    # сохранять нечего). Имя со таймстемпом, чтобы не затирать предыдущие дампы.
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if os.path.exists(state_path):
        corrupted_copy = f"{state_path}.corrupted-{ts}"
        try:
            shutil.copy2(state_path, corrupted_copy)
            print(f"[recover] повреждённый state.json сохранён для разбора: {corrupted_copy}")
        except OSError as e:
            # Не смогли сохранить дамп — НЕ блокируем восстановление (дамп — nice-to-have,
            # основная задача — вернуть рабочий state.json). Логируем и идём дальше.
            print(f"[recover] не смог сохранить .corrupted копию: {e} (продолжаем)")

    # Копируем бэкап в рабочий путь.
    try:
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
        shutil.copy2(src, state_path)
    except OSError as e:
        err = f"failed to copy backup {backup_ts}: {e}"
        print(f"[recover] {err}")
        return {"recovered": False, "backup_ts": backup_ts, "error": err}

    print(
        f"[recover] state.json восстановлен из бэкапа {backup_ts} "
        f"(повреждённый файл сохранён как state.json.corrupted-{ts})"
    )
    return {"recovered": True, "backup_ts": backup_ts, "error": None}
