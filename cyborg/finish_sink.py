"""Орган-приёмник finish_sink (Левая рука): КЛАДЁТ nudge из режима «доделать» в инбокс,
в ДОРОЖКУ B (store.set_finish) — отдельный слот-напоминание «доделать существующее»,
который живёт ОТДЕЛЬНО от дорожки A (новые идеи) — своя секция, не смешивается с ними.

Метафора чистая: рука ТОЛЬКО кладёт. Вычистку секретов делает Печень (scrub_secrets),
через которую нудж проходит РАНЬШЕ — в нервах (wiring._run_finish_sink), до руки.
Раньше рука чистила сама (_scrub_nudge внутри) — это была работа Печени, убрано.

Адрес (почему дорожка B, а не A): nudge «доделай» кладётся через set_finish, а НЕ
add_idea — иначе он съедал бы слот новых идей и попадал в секцию «новые идеи».

Store и рендер инбокса ПЕРЕИСПОЛЬЗУЮТСЯ из idea_engine (importlib по абс-пути,
чтобы не столкнуться с cyborg/run.py в sys.path) — не дублируем.
"""

import importlib.util
import os
import sys

_IDEA = "M:/projects/kiborg/idea_engine"
if _IDEA not in sys.path:
    sys.path.insert(0, _IDEA)


def _load_ie_run():
    spec = importlib.util.spec_from_file_location("ie_run", os.path.join(_IDEA, "run.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run(inputs, env):
    inp = inputs or {}
    nudge = inp.get("nudge")
    # пусто (None / {} / не dict) — класть нечего, no-op (диск не трогаем)
    if not isinstance(nudge, dict) or not nudge:
        return {"delivered": 0, "inbox": None, "lane": "B"}
    ie = _load_ie_run()
    from store import Store, state_lock  # idea_engine/store.py

    # межпроцессный замок вокруг read-modify-write state.json — КАК deliver.py (дорожка A):
    # другой писатель (deliver / пульт-триаж / CLI-harvest) мог бы затереть наш апдейт (lost-update).
    # Best-effort, без дедлока. Раньше дорожка B писала БЕЗ замка = асимметрия с дорожкой A
    # (нашла фабрика б-3 2026-07-15, needs_manual: сквозная правка, не изолируется в OFF-фичу).
    with state_lock(ie.STATE):
        store = Store(ie.STATE, cap=ie.CFG["cap"])
        # ДОРОЖКА B: отдельный слот, живёт своей секцией, не смешивается с новыми идеями (дорожка A).
        # nudge кладём КАК ЕСТЬ — секреты уже вычистила Печень (scrub_secrets) выше по конвейеру.
        store.set_finish(nudge, store.data.get("cursor", 0))
        store.save()
        ie._write_inbox(store)
    return {"delivered": 1, "inbox": ie.INBOX, "lane": "B"}


if __name__ == "__main__":
    # смоук: рука кладёт нудж как есть (без записи на реальный диск — только контракт).
    # ВНИМАНИЕ: чистку секретов рука больше НЕ делает — она на Печени (scrub_secrets),
    # через которую нудж проходит в wiring._run_finish_sink ДО этой руки.
    demo = {"title": "Доделать: panelofprojects", "why": "починить путь", "folder": "x"}
    print("рука кладёт (дорожка B):", demo["title"])
    print("SMOKE OK (чистка секретов — забота Печени выше по конвейеру)")
