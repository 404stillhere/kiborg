"""ПЕЧЕНЬ + РУКА: вычистка секретов (scrub) и доставка (deliver/finish_sink).

Вынесено из монолита wiring.py. Органы scrub_secrets/deliver/finish_sink — патчатся в
тестах (test_scrub: `wiring._run_scrub`, test_finish_sink: `wiring._run_finish_sink`),
читаем через фасад.
"""


def _liver_clean(rec):
    """Печень (scrub_secrets): прогоняет текстовые поля записи через вычистку секретов.
    Чистка — работа Печени, не руки. Возвращает копию с вычищенными title/why."""
    import wiring

    clean = dict(rec)
    for f in ("title", "why"):
        if isinstance(clean.get(f), str):
            clean[f] = wiring.scrub_secrets.scrub_text(clean[f])
    return clean


def _run_deliver(inputs, env):
    import wiring

    return wiring.deliver.run(inputs, env)


def _run_finish_sink(inputs, env):
    import wiring

    # Нервы ведут нудж СНАЧАЛА через Печень (scrub_secrets), ПОТОМ в руку (finish_sink).
    # Рука больше не чистит сама (раньше _scrub_nudge был внутри finish_sink — рука делала
    # работу Печени). Метафора честная: Печень фильтрует, Рука кладёт, нервы соединяют.
    inp = inputs or {}
    nudge = inp.get("nudge")
    if isinstance(nudge, dict) and nudge:
        inp = {**inp, "nudge": _liver_clean(nudge)}  # Печень чистит нудж
    return wiring.finish_sink.run(inp, env)  # Рука кладёт уже вычищенное


def _run_scrub(inputs, env):
    import wiring

    inp = inputs or {}
    ideas = list(inp.get("ideas_polished") or inp.get("ideas_best") or inp.get("ideas") or [])
    out, red = [], 0
    for idea in ideas:
        if isinstance(idea, dict):
            clean = dict(idea)
            for f in ("title", "why"):
                if isinstance(clean.get(f), str):
                    s = wiring.scrub_secrets.scrub_text(clean[f])
                    if s != clean[f]:
                        red += 1
                    clean[f] = s
            out.append(clean)
        else:
            out.append(idea)
    return {"ideas_safe": out, "redacted": red}
