"""НОГИ: режим «доделай» (finish_step) с персистентным курсором ротации проектов.

Вынесено из монолита wiring.py. Орган finish_step и константы _CURSOR_FILE/RECON/SKIP_FOLDERS
патчатся в тестах (test_registry: `wiring.finish_step = FakeFS`, `wiring._CURSOR_FILE = ...`) —
читаем через фасад.
"""

import json
import os


def _run_finish(inputs, env):
    import wiring

    # ПАМЯТЬ (2026-07-13): курсор — тоже работа Мозга, не Ног. Ноги (finish_step) просто идут
    # туда, куда сказали; ПОМНИТЬ, на каком проекте остановились, — не их дело. Настоящего
    # Мозг-органа в цепочке «доделать» нет (finish_step сам источник), поэтому решение живёт
    # тут, в нервах — на пульте помечено честным узлом «🧠 Мозг (в нервах)» перед Ногами.
    # Курсор ПЕРСИСТИТСЯ между прогонами — иначе finish_step всегда возвращал первый проект
    # (память per-run, «cursor» в ней не появлялся; finish_step отдаёт «next_cursor»). Теперь
    # «доделай» реально ротирует по проектам бэклога.
    cursor_file = wiring._CURSOR_FILE
    cursor = 0
    try:
        with open(cursor_file, encoding="utf-8") as f:
            cursor = int(json.load(f).get("cursor", 0))
    except Exception:
        pass
    out = wiring.finish_step.run(
        inputs, {"recon_path": wiring.RECON, "cursor": cursor, "skip_folders": wiring.SKIP_FOLDERS}
    )
    try:
        os.makedirs(os.path.dirname(cursor_file), exist_ok=True)
        with open(cursor_file, "w", encoding="utf-8") as f:
            json.dump({"cursor": int(out.get("next_cursor", cursor + 1))}, f)
    except Exception:
        pass
    return out
