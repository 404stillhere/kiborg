"""Орган-приёмник (sink) для АВТОНОМНОГО режима: копит идеи в копилку БЕЗ потолка.

В отличие от deliver (инбокс idea_engine, потолок 3 + обратная тяга), этот sink складывает
идеи в копилку, что растёт без предела — чтобы за автономный прогон (пока юзер ушёл) набралась
гора идей. Контракт органа тот же: consumes ideas_safe (или ideas), produces delivered.

Секреты уже вычищены органом scrub_secrets выше по цепочке; здесь ничего в живой прод не пишем —
только в свой файл копилки под cyborg/data/.
"""
import stash


def run(inputs, env):
    env = env if isinstance(env, dict) else {}
    # LLM-режим: ключ есть, ждём идеи от модели. brain!=llm тогда = осечка парса Gemini
    # (ideate упал на болванку «Идея по мотиву: <заголовок>») — такое в копилку НЕ копим,
    # это шум, а не идея. Без ключа (stub-режим) болванки ожидаемы — копим как есть.
    llm_mode = callable(env.get("content_llm") or env.get("llm"))
    inp = inputs or {}
    ideas = list(inp.get("ideas_safe") or inp.get("ideas") or [])
    st = stash.Stash()
    added, dropped_stub = 0, 0
    for idea in ideas:
        if not isinstance(idea, dict):
            continue
        if llm_mode and idea.get("brain") == "stub":
            dropped_stub += 1
            continue
        if st.add(idea):
            added += 1
    st.save()
    return {"delivered": added, "stash": st.md, "stash_total": len(st.ideas),
            "dropped_stub": dropped_stub}


if __name__ == "__main__":
    demo = run({"ideas_safe": [{"title": "идея из смоука sink", "why": "проверка", "effort": "легко"}]}, {})
    print(demo)
