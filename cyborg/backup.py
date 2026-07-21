"""Автоматическое резервное копирование state.json + seen_items.json.

harvest_runner.main() перед прогоном зовёт backup_state() — копирует ОБА файла в
config.BACKUPS_DIR/<TS>/ (TS = %Y-%m-%d_%H%M%S). Формат — простые копии (не tar.gz):
diff-able, прозрачно, легко восстановить вручную. Ротация: храним последние config.MAX_BACKUPS
копий (по имени подкаталога-TS), старше — удаляем.

Зачем: state.json меняется под state_lock (deliver/finish_sink), seen_items.json — в mark_seen.
При поврежении (крах в середине записи, диск полный, ручная правка с ошибкой) — восстановление
из последнего бэкапа через cyborg/restore_backup.py. Без бэкапа потеря state.json = потеря всего
инбокса/seen-истории.

Что копируется (по абсолютным путям из config + seen_items):
  - config.IE_STATE_JSON   = idea_engine/data/state.json
  - seen_items.PATH        = cyborg/data/seen_items.json

Надёжность: любая ошибка (нет файла, нет прав) → print + return None. Бэкап НЕ должен ронять
рабочий прогон киборга. Файл может отсутствовать на свежей установке — это норма, просто skip.
"""

import datetime
import os
import shutil

import config
import seen_items


def _list_backups():
    """Список подкаталогов-TS в BACKUPS_DIR (упорядоченный, убывание = свежие первыми)."""
    try:
        names = [d for d in os.listdir(config.BACKUPS_DIR) if os.path.isdir(os.path.join(config.BACKUPS_DIR, d))]
    except FileNotFoundError:
        return []
    # имена вида 2026-07-21_054700 — лексикографическая сортировка = хронологическая
    return sorted(names, reverse=True)


def _prune_old_backups():
    """Удалить бэкапы старше config.MAX_BACKUPS (по имени, самые свежие оставляем)."""
    names = _list_backups()  # свежие первыми
    if len(names) <= config.MAX_BACKUPS:
        return
    for old in names[config.MAX_BACKUPS :]:  # все, кто за пределами лимита
        old_path = os.path.join(config.BACKUPS_DIR, old)
        try:
            shutil.rmtree(old_path)
        except OSError as e:
            print(f"[backup] не смог удалить старый бэкап {old}: {e} (не критично, продолжаем)")


def backup_state():
    """Скопировать state.json + seen_items.json в BACKUPS_DIR/<TS>/, ротаровать старые.

    Возвращает путь к созданному бэкапу (для лога) или None при ошибке/отсутствии файлов.
    НЕ выбрасывает — бэкап не должен ронять прогон киборга. Внутри печатает прогресс/причины.
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target_dir = os.path.join(config.BACKUPS_DIR, ts)
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as e:
        print(f"[backup] не смог создать {target_dir}: {e} — бэкап пропущен")
        return None

    # Что копируем: (путь-источник, куда-в-бэкапе). Имя файла сохраняется.
    sources = [
        config.IE_STATE_JSON,  # idea_engine/data/state.json
        seen_items.PATH,  # cyborg/data/seen_items.json
    ]
    copied = 0
    for src in sources:
        if not os.path.exists(src):
            continue  # свежая установка / файла ещё нет — skip, не ошибка
        try:
            shutil.copy2(src, os.path.join(target_dir, os.path.basename(src)))
            copied += 1
        except OSError as e:
            print(f"[backup] не смог скопировать {src}: {e} (продолжаем с остальными)")

    if copied == 0:
        # Ни одного файла не скопировано (оба отсутствуют). Удаляем пустой бэкап-каталог,
        # чтобы не плодить пустые TS-подкаталоги при каждом прогоне.
        try:
            os.rmdir(target_dir)
        except OSError:
            pass
        return None

    _prune_old_backups()
    return target_dir
