"""CLI-утилита восстановления state.json + seen_items.json из бэкапа.

Запуск:
  python cyborg/restore_backup.py              — интерактив: показать список, спросить номер
  python cyborg/restore_backup.py --list       — только напечатать список бэкапов
  python cyborg/restore_backup.py --auto       — авто-восстановление ЕСЛИ state.json повреждён
                                                 (no-op если он валиден). Неинтерактивно, для cron/скриптов.
  python cyborg/restore_backup.py 2026-07-21_054700
                                               — восстановить указанный (без подтверждения)

Что делает восстановление:
  1. Копирует ТЕКУЩИЕ state.json и seen_items.json в .pre-restore-<TS> рядом (страховка —
     если восстановили не то, можно откатить).
  2. Копирует state.json и seen_items.json из выбранного бэкапа в их реальные пути
     (config.IE_STATE_JSON, seen_items.PATH).

--auto ОТЛИЧАЕТСЯ от восстановления по имени: оно срабатывает ТОЛЬКО при повреждении
state.json (битый JSON или отсутствие файла). Использует ту же логику, что harvest_runner.main()
в начале прогона — удобно прогнать вручную из cron'а или скрипта без интерактива.

ВАЖНО: восстановить НУЖНО оба файла вместе (seen_items и state.json согласованы — seen_items
запоминает posts, которые уже ушли в идеи через ideate). Восстановить один без другого = риск
дублирования идей в следующий прогон.

Не запускать во время прогона киборга — state_lock НЕ блокирует (мы пишем в обход Store),
можем попасть в середину чужой read-modify-write и потерять данные. Сначала останови harvest
(кнопка Стоп в пульте или kill процесса).
"""

import datetime
import os
import shutil
import sys

# path-bootstrap: этот скрипт лежит в cyborg/, но запускается как `python cyborg/restore_backup.py`
# из корня проекта — нужен cyborg/ в sys.path для import config/seen_items.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootstrap_paths  # noqa: E402

bootstrap_paths.ensure_project_paths()

import config  # noqa: E402
import recover_state  # noqa: E402
import seen_items  # noqa: E402


def _list_backups():
    """Список подкаталогов-TS в BACKUPS_DIR, убывание (свежие первыми)."""
    try:
        names = [d for d in os.listdir(config.BACKUPS_DIR) if os.path.isdir(os.path.join(config.BACKUPS_DIR, d))]
    except FileNotFoundError:
        return []
    return sorted(names, reverse=True)


def _print_list():
    names = _list_backups()
    if not names:
        print(f"бэкапов нет ({config.BACKUPS_DIR} пуст или отсутствует)")
        return
    print(f"бэкапы в {config.BACKUPS_DIR} (свежие сверху):")
    for i, name in enumerate(names, 1):
        bdir = os.path.join(config.BACKUPS_DIR, name)
        files = sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []
        print(f"  [{i}] {name}  ({', '.join(files) or 'пусто'})")


def _pre_restore_copy(original_path, ts):
    """Скопировать текущий файл в <path>.pre-restore-<TS> перед перезаписью (страховка)."""
    if not os.path.exists(original_path):
        return None
    backup = f"{original_path}.pre-restore-{ts}"
    shutil.copy2(original_path, backup)
    return backup


def restore(backup_name):
    """Восстановить state.json + seen_items.json из бэкапа backup_name (TS-подкаталог)."""
    src_dir = os.path.join(config.BACKUPS_DIR, backup_name)
    if not os.path.isdir(src_dir):
        print(f"ошибка: бэкап '{backup_name}' не найден в {config.BACKUPS_DIR}")
        return False

    # Куда копируем (реальные пути state.json + seen_items.json).
    targets = {
        "state.json": config.IE_STATE_JSON,
        "seen_items.json": seen_items.PATH,
    }
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")

    # Шаг 1: pre-restore страховка текущих файлов.
    pre_copied = []
    for fname, dst in targets.items():
        if os.path.exists(src_dir) and os.path.exists(os.path.join(src_dir, fname)):
            saved = _pre_restore_copy(dst, ts)
            if saved:
                pre_copied.append(saved)

    # Шаг 2: копируем из бэкапа в реальные пути.
    restored = []
    for fname, dst in targets.items():
        src = os.path.join(src_dir, fname)
        if not os.path.exists(src):
            print(f"  предупред: в бэкапе нет {fname} (skip)")
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(f"{fname} → {dst}")

    if not restored:
        print(f"ошибка: бэкап '{backup_name}' пустой (нет state.json/seen_items.json)")
        return False

    print(f"восстановлено из {backup_name}:")
    for line in restored:
        print(f"  {line}")
    if pre_copied:
        print("\nстраховка (текущие файлы сохранены перед перезаписью):")
        for p in pre_copied:
            print(f"  {p}")
    return True


def _interactive():
    """Показать список, спросить номер у юзера, восстановить."""
    names = _list_backups()
    if not names:
        print(f"бэкапов нет ({config.BACKUPS_DIR} пуст или отсутствует) — восстанавливать нечего")
        return
    _print_list()
    print()
    try:
        choice = input("какой восстановить? [номер / Enter=отмена]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nотмена")
        return
    if not choice:
        print("отмена")
        return
    try:
        idx = int(choice)
        if not (1 <= idx <= len(names)):
            print(f"номер должен быть 1..{len(names)}")
            return
    except ValueError:
        print(f"нужно число (1..{len(names)})")
        return
    name = names[idx - 1]
    try:
        confirm = input(f"перезаписать ТЕКУЩИЕ state.json/seen_items.json из {name}? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nотмена")
        return
    if confirm != "y":
        print("отмена")
        return
    restore(name)


def main(argv):
    if not argv:
        _interactive()
        return 0
    if argv[0] in ("-l", "--list", "list"):
        _print_list()
        return 0
    if argv[0] == "--auto":
        # Неинтерактивное авто-восстановление: ТОЛЬКО если state.json повреждён/отсутствует.
        # Та же логика, что harvest_runner.main() в начале прогона — удобно для cron/скриптов.
        # Возвращает 0 даже при «нет бэкапа» (это не ошибка утилиты, а состояние проекта).
        result = recover_state.auto_recover_state_if_needed(
            config.IE_STATE_JSON, config.BACKUPS_DIR, config.MAX_BACKUPS
        )
        if result["recovered"]:
            print(f"OK: state.json восстановлен из бэкапа {result['backup_ts']}")
            return 0
        if result["error"]:
            # state.json повреждён, но восстановить не удалось — это реальная проблема,
            # ненулевой код возврата для cron-скриптов, которые могут поймать и алертнуть.
            print(f"WARN: восстановление не выполнено — {result['error']}")
            return 1
        # state.json валиден — ничего делать было не нужно.
        print("OK: state.json валиден, восстановление не требуется")
        return 0
    if argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    # Иначе argv[0] — имя бэкапа (TS). Восстановить без подтверждения (для скриптов/cron).
    ok = restore(argv[0])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
