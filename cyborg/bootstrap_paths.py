"""Единый path-bootstrap для cyborg/ фасадов (wiring.py, harvest.py).

ПРОБЛЕМА, которую решает этот модуль: в проекте нет пакетной структуры (нет cyborg/__init__.py),
импорты идут через голые `import X` (organs, rejected, advisors, ...). Часть этих модулей живёт
в cyborg/, часть — в idea_engine/ (на уровне выше). Чтобы `import wiring` или `import harvest`
работали, ОБА каталога должны быть в sys.path.

Раньше эта логика была размазана inline по фасадам:
  - wiring.py добавлял idea_engine/ + cyborg/ (и потому работал автономно)
  - harvest.py добавлял ТОЛЬКО cyborg/ (идея-движок рассчитывал на чужой `import wiring`)
Это значило, что `import harvest` без предварительного `import wiring` падал с
`ModuleNotFoundError: No module named 'rejected'` (rejected живёт в idea_engine/).

РЕШЕНИЕ: вынести path-init сюда и звать из обоих фасадов первой строкой после импорта os/sys.
Функция идемпотентна (повторный вызов — no-op), не печатает ничего, не трогает бизнес-логику.

Модуль НЕ требует самого себя в sys.path заранее: его собственный __file__ — отправная точка,
от него вычисляются cyborg/ и idea_engine/. Так как фасады (wiring.py, harvest.py) лежат в
cyborg/, Python при их импорте уже имеет каталог cyborg/ доступным (или они сами его добавят
через sys.path.insert перед `import bootstrap_paths` — см. порядок в фасадах).
"""

import os
import sys

# Кэш: не повторять работу, если уже отработало (sys.path-проверка дублирует это, но кэш
# ещё и защищает от гонки при двух вызовах подряд в разных контекстах — лишний insert
# с `if X not in sys.path` мог бы попасть между проверкой и вставкой в потоке (теоретически).
_DONE = False
_DIRS_DONE = False  # отдельный кэш для ensure_data_dirs (она тоже идемпотентна)


def ensure_project_paths():
    """Добавить cyborg/ и idea_engine/ в sys.path, если их там ещё нет. Идемпотентно.

    Вычисляет пути от __file__ этого модуля (cyborg/bootstrap_paths.py):
      cyborg/      = os.path.dirname(__file__)
      idea_engine/ = cyborg/../idea_engine
    Так bootstrap работает с любого CWD и на любой ОС (относительные пути, не хардкод Windows).
    Безопасен при повторных вызовах: кэш + `if path not in sys.path`.
    """
    global _DONE
    if _DONE:
        return
    here = os.path.dirname(os.path.abspath(__file__))  # .../kiborg/cyborg
    idea = os.path.abspath(os.path.join(here, "..", "idea_engine"))  # .../kiborg/idea_engine
    if here not in sys.path:
        sys.path.insert(0, here)
    if idea not in sys.path:
        sys.path.insert(0, idea)
    _DONE = True


def ensure_data_dirs():
    """Создать cyborg/data/, idea_engine/data/, cyborg/data/backups/ при первом запуске.

    Идемпотентна: повторный вызов не падает, файлы внутри директорий не трогает.
    Предотвращает падение backup_state()/serve.py на свежем клоне, где data/ ещё не существует.
    """
    global _DIRS_DONE
    if _DIRS_DONE:
        return
    import config  # noqa: E402  # isort: skip

    for d in (config.CYBORG_DATA_DIR, config.IDEA_ENGINE_DATA_DIR, config.BACKUPS_DIR):
        os.makedirs(d, exist_ok=True)
    _DIRS_DONE = True
