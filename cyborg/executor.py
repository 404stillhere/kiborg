"""Исполнитель — безопасно вызывает орган.

Прод-гейт (safe_mode): органы, что пишут в живой прод, НЕ запускаются автономно
(возвращают skipped) — доктрина киборга: живой проект не трогаем без явного разрешения.
Органы, требующие ключ, которого нет (и без stub-фолбэка), тоже skipped.
Падение органа не роняет киборга — возвращаем {'error': ...} для перепланирования.
"""


def execute(organ, inputs, env, safe_mode=True):
    needs = organ.needs or {}
    if safe_mode and needs.get("prod"):
        return {"skipped": "prod-gated (safe_mode)"}
    key_name = needs.get("key")
    if key_name and safe_mode and not needs.get("stub_ok"):
        has_key = isinstance(env, dict) and (env.get(key_name) or env.get("llm"))
        if not has_key:
            return {"skipped": f"нет ключа {key_name}"}
    try:
        result = organ.run(inputs, env)
        if not isinstance(result, dict):
            result = {"result": result}
        return result
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
