"""Орган-приёмник (sink): доставляет идеи в ИНБОКС через очередь idea_engine
(потолок 3 + обратная тяга + inbox.md). Это делает киборга реально полезным —
идеи не теряются в памяти, а копятся в инбоксе с backpressure, как в idea_engine,
но приходят через общий оркестратор. Устраняет дубль (cyborg больше не гоняет органы
вхолостую) и подключает осиротевшую доставку.

Переиспользует store.Store и _write_inbox из idea_engine — НЕ дублирует их заново.
"""
import importlib.util
import os
import sys

_IDEA = "M:/projects/kiborg/idea_engine"
if _IDEA not in sys.path:
    sys.path.insert(0, _IDEA)


def _load_ie_run():
    # грузим idea_engine/run.py по абсолютному пути под уникальным именем,
    # чтобы не столкнуться с cyborg/run.py в sys.path
    spec = importlib.util.spec_from_file_location("ie_run", os.path.join(_IDEA, "run.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run(inputs, env):
    ie = _load_ie_run()
    from store import Store  # idea_engine/store.py

    # принимаем и очищенные (ideas_safe от scrub), и сырые (ideas) — что дали
    inp = inputs or {}
    ideas = list(inp.get("ideas_safe") or inp.get("ideas") or [])
    store = Store(ie.STATE, cap=ie.CFG["cap"])
    store.data["tick"] += 1
    added = 0
    for idea in ideas:
        if not isinstance(idea, dict):
            continue
        idea.setdefault("kind", "new")
        idea.setdefault("source", "cyborg")
        if store.add_idea(idea):        # обратная тяга: не влезет сверх потолка
            added += 1
        if not store.has_room():
            break
    store.save()
    ie._write_inbox(store)
    return {"delivered": added, "inbox": ie.INBOX, "queue_open": len(store.open_ideas())}


if __name__ == "__main__":
    demo = run({"ideas": [{"title": "тестовая идея", "why": "smoke", "effort": "легко"}]}, {})
    print(demo)
