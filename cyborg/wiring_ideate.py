"""МОЗГ-генератор: ideate (порождение идей из сырья).

Вынесено из монолита wiring.py. seen_items/ideate — органы, патчатся в тестах
(test_wiring: `wiring.ideate.run = ...`), читаем через фасад. _content_llm —
чистый хелпер из wiring_runtime (нет зависимости от фасада, импортируем напрямую).
"""

from wiring_runtime import _content_llm


def _run_ideate(inputs, env):
    import wiring

    inp = inputs or {}
    # ПАМЯТЬ — работа Мозга, не Глаз (2026-07-13, переехало из _run_collect). Фильтр «уже
    # видели» — ТОЛЬКО когда явно попросили (харвест ставит флаг в env). Интерактивный
    # «приноси идеи» (панель, ручной клик) флаг не ставит — юзер жмёт кнопку, ожидая идей
    # СЕЙЧАС, а не «а тут всё уже старое, пропускаю». filter_fresh отмечает виденным ровно
    # то, что реально уходит на генерацию — не раньше.
    fresh = None
    if env.get("filter_seen_items") and inp.get("items"):
        inp = dict(inp)
        fresh = wiring.seen_items.filter_fresh(inp["items"], mark=False)  # фильтруем БЕЗ пометки
        inp["items"] = fresh
    e = {"k": 12}  # режим «максимум качества»: генерим 12 кандидатов — судье есть из чего отобрать лучшее
    llm = _content_llm(env)
    if llm:
        e["llm"] = llm
    if env.get("direction"):
        e["direction"] = env["direction"]  # руль темы долетает до генератора
    if env.get("on_progress"):
        e["on_progress"] = env["on_progress"]  # живой суб-прогресс долетает до органа (иначе молчит)
    out = wiring.ideate.run(inp, e)
    # Метим сырьё виденным ТОЛЬКО ПОСЛЕ генерации и лишь если она удалась. При живом ключе
    # (llm_mode) осечка парса / обрыв даёт brain='stub' — НЕ метим, чтобы посты не сгорели зря:
    # сбой транзиентный, повторим на следующем тике (раньше метили ДО генерации — сжигали). Без
    # ключа stub ожидаем — метим как обычно, чтобы не крутить одни и те же заголовки.
    if fresh:
        ideas = out.get("ideas") or []
        produced_real = any(isinstance(i, dict) and i.get("brain") != "stub" for i in ideas)
        if produced_real or not callable(llm):
            wiring.seen_items.mark_seen(fresh)
    # PROVIDER — кто РЕАЛЬНО ответил в генераторе (muse-spark/deepseek/nemotron — цепочка
    # closerouter, см. keychain._SPEC). Полезно видеть, какое плечо сработало: muse-spark=первичная,
    # остальное=фолбэк при отлёте первичной. ask_llm.last_provider ставит _run_chain от organ.js
    # result.provider; поднимаем в out органа → orchestrator пробросит в run-выхлоп →
    # harvest._degrade_note рендерит «модель=…». Только при живой модели (stub-режим без ключа
    # провайдера не имеет).
    if callable(llm):
        try:
            import ask_llm as _ask

            prov = _ask.last_provider
        except Exception:
            prov = ""
        if prov:
            out["provider"] = prov
    return out
