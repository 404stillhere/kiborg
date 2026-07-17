"""Орган-приёмник (sink): доставляет идеи в ИНБОКС через очередь idea_engine
(cap=0 — БЕЗ потолка + inbox.md; потолок снят 2026-07-13, механика cap/backpressure в
store.py цела для тестов, но при cap=0 не срабатывает). Это делает киборга реально
полезным — идеи не теряются в памяти, а копятся в инбоксе одной кучей и приходят через
общий оркестратор. Устраняет дубль (cyborg больше не гоняет органы вхолостую) и
подключает осиротевшую доставку.

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
    from store import Store, state_lock  # idea_engine/store.py

    env = env if isinstance(env, dict) else {}
    # LLM-режим: ключ есть, ждём идеи от живой модели. Тогда brain!=llm = осечка парса / обрыв
    # сети: ideate упал на болванку «Идея по мотиву: <заголовок>». Такое в инбокс НЕ доставляем —
    # это шум, а не идея (иначе прогон рапортует «доставлено N» на мусоре — root fail-open). Без
    # ключа (stub-режим) болванки ожидаемы — доставляем как есть.
    llm_mode = callable(env.get("content_llm") or env.get("llm"))
    # принимаем и очищенные (ideas_safe от scrub), и сырые (ideas) — что дали
    inp = inputs or {}
    ideas = list(inp.get("ideas_safe") or inp.get("ideas") or [])
    added, dropped_stub, dropped_dup, queue_open = 0, 0, 0, 0
    # Фильтр болванок: бросаем stub-идеи только если в партии есть ХОТЯ БЫ ОДНА настоящая
    # LLM-идея (brain=llm). Если ВСЕ болванки — LLM упал в моменте (402/сеть/пустой ответ)
    # несмотря на живой ключ; выбросить всё = ноль в инбоксе, хотя болванки лучше пустоты.
    # Так при нулевом балансе киборг молча деградирует на детерминированный арбитр (rank_ideas
    # без модели) и доставляет болванки, а не молчит. Стоит восстановиться балансу — снова
    # фильтрует (has_llm_ideas снова True). Без ключа (stub_mode штатный) — доставляем как есть.
    has_llm_ideas = llm_mode and any(
        isinstance(i, dict) and i.get("brain") == "llm" for i in ideas
    )
    # межпроцессный замок вокруг read-modify-write state.json: другой процесс (пульт-триаж /
    # CLI-harvest) мог бы затереть наш апдейт (lost-update; порчу файла уже снял atomic save).
    # Best-effort, без дедлока — снижает окно гонки, не гарантирует полную сериализацию.
    with state_lock(ie.STATE):
        store = Store(ie.STATE, cap=ie.CFG["cap"])
        store.data["tick"] += 1
        for idea in ideas:
            if not isinstance(idea, dict):
                continue
            if has_llm_ideas and idea.get("brain") == "stub":
                dropped_stub += 1       # болванка при живом ключе = шум, в инбокс не пускаем
                continue
            idea.setdefault("kind", "new")
            idea.setdefault("source", "cyborg")
            if not store.has_room():
                break
            if store.add_idea(idea):    # обратная тяга: не влезет сверх потолка
                added += 1
            else:
                dropped_dup += 1        # идея отклонена как дубликат
        store.save()
        ie._write_inbox(store)
        queue_open = len(store.open_ideas())
    return {"delivered": added, "inbox": ie.INBOX, "queue_open": queue_open,
            "dropped_stub": dropped_stub, "dropped_dup": dropped_dup}


if __name__ == "__main__":
    demo = run({"ideas": [{"title": "тестовая идея", "why": "smoke", "effort": "легко"}]}, {})
    print(demo)
