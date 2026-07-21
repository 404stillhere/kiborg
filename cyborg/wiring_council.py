"""СОВЕТ на шаге отбора идей: rank + council + редактор читаемости.

Вынесено из монолита wiring.py — самая толстая зона (отбор идей взвешенным советом
mind.deliberate, интуиция без потолка токенов, фолбэк на rank_ideas, редактор
читаемости). Органы advisors/mind/rank_ideas/readability_gate — патчатся в тестах,
читаем через фасад.
"""

import wiring  # noqa: E402  (advisors нужен на module level — base class _IntuitionNoCap)
from wiring_runtime import _content_llm


class _IntuitionNoCap(wiring.advisors.AskLlmAdvisor):
    """Интуиция (ask_llm) БЕЗ потолка на ответ (реш. юзера 2026-07-13: «убрать ограничение
    вообще»). Рассуждающие модели closerouter при max_tokens=256 тратят весь лимит на скрытое
    рассуждение и возвращают пусто → интуиция молчит. Проверено: без max_tokens deepseek
    досказывает рассуждение (~1000 токенов) и отдаёт баллы.

    Отличие от родителя ровно одно — нет потолка ответа. Наследуем _ask как есть, гасим лишь
    `_MAX_TOKENS` (родитель кладёт max_tokens в payload, только когда он не None). Копию _ask
    больше не держим — параметр уже в advisors.AskLlmAdvisor."""

    _MAX_TOKENS = None


def _council_no_cap(context=None):
    """Тот же совет (advisors.build_council), но голос интуиции — БЕЗ потолка (_IntuitionNoCap).
    Арбитр и оркестр берём как есть из их модуля; подменяем только ask_llm."""
    return [
        _IntuitionNoCap() if getattr(a, "name", "") == "ask_llm" else a for a in wiring.advisors.build_council(context)
    ]


def _rank_by_council(inputs, env, keep):
    """Отбор топ-keep идей ВЗВЕШЕННЫМ СОВЕТОМ (mind.deliberate), а не одиночным судьёй.

    Совет = арбитр rank_ideas (0.41) + интуиция ask_llm (0.39) + оркестр (0.20). Оркестр
    голосует ВСЕГДА, когда есть ключи (реш. юзера: совет зовётся всегда, а не по сомнению
    интуиции — «умный сомневается всегда»). Потому deliberate (плоский, все голосуют
    безусловно), а НЕ think (там оркестр за эскалацией). Совет ставит балл каждой идее,
    берём топ-keep по итоговому баллу — так форма ideas_best (список) цела,
    downstream (scrub/deliver) не трогаем.

    Возвращает {'ideas_best':[...]} когда проголосовал хоть один советник (арбитр внутри
    совета (mind.deliberate) опрашивается первым, живой моделью — его результат переиспользуем,
    чтобы НЕ звать rank_ideas.run повторно). solo=True в метаданных = по факту судил один арбитр.
    None только если воздержались ВСЕ (degraded) -> вызыватель идёт на плоский rank_ideas."""
    ideas = list((inputs or {}).get("ideas") or [])
    if len(ideas) <= keep:
        return {"ideas_best": ideas}  # отбирать не из чего — отдаём как есть
    # варианты для совета: копия идей с явным id=индекс, чтобы вернуть ИСХОДНЫЕ дикты по id
    options, orig = [], {}
    for i, d in enumerate(ideas):
        base = dict(d) if isinstance(d, dict) else {"title": str(d)}
        options.append({**base, "id": i})
        orig[i] = d
    # Оркестр теперь голосует на КАЖДОМ отборе (горячий путь) и судит весь пул идей подряд.
    # Чтобы 12 идей × рецензент не вылезли за таймаут пульта (180с): гоним ВСЕХ рецензентов
    # параллельно (max_workers = число моделей) и держим короткий бюджет на идею. Настройки
    # кладём в cfg здесь — keychain/advisors их принимают, но сами не трогаются.
    orch = env.get("orchestra")
    if isinstance(orch, dict) and orch.get("models"):
        orch = {**orch, "max_workers": len(orch["models"]), "timeout_sec": int(env.get("orchestra_timeout_sec", 45))}
    context = {
        "content_llm": _content_llm(env),  # оживляет арбитра живой моделью (иначе фолбэк-порядок)
        "llm_chain": env.get("llm_chain"),  # оживляет интуицию (цепочка провайдеров с ключами)
        "orchestra": orch,  # оркестр: голосует всегда (параллельно, короткий бюджет)
        "llm_timeout_ms": env.get("llm_timeout_ms", 45000),
        "direction": env.get("direction"),  # руль темы: арбитр читает из ctx, интуиция/оркестр — из вопроса
    }
    question = "Отбери лучшие идеи для доставки: оригинальность, польза, выполнимость."
    if env.get("direction"):  # направление в вопрос → его видят интуиция и оркестр
        question += f" Приоритет — идеи в направлении «{env['direction']}»."
    # живой суб-прогресс: отбор советом — САМЫЙ медленный шаг (рецензенты × идеи, минуты), а
    # внутренний цикл в mind.deliberate (заморожен) отсюда не видно — даём хотя бы одну строку
    # «совет судит N идей», чтобы пульт не молчал на самом долгом органе.
    op = env.get("on_progress")
    if callable(op):
        n_rev = len(orch["models"]) if isinstance(orch, dict) and orch.get("models") else 0
        op("совет судит %d идей%s" % (len(options), (" (%d рецензентов)" % n_rev) if n_rev else ""))
    # deliberate = плоский совет: арбитр + интуиция + оркестр голосуют ВСЕ и ВСЕГДА (кто без
    # ключа — сам воздержится). Не think: там оркестр спал, пока интуиция не засомневается —
    # ровно та «пропущу совет, раз уверен» логика, которую юзер не хотел.
    verdict = wiring.mind.deliberate(question, options, _council_no_cap(context), context)
    live = verdict.get("live") or []
    if verdict.get("degraded") or not live:  # никто не проголосовал -> плоский откат на судью
        return None
    # Арбитр внутри совета (mind.deliberate) УЖЕ отработал живой моделью (его опрашивают первым). Поэтому и
    # когда голос один (интуиция/оркестр промолчали), берём готовый результат ОТСЮДА, а не зовём
    # rank_ideas.run повторно — иначе второй платный вызов той же модели (нашёл скептик 2026-07-13).
    scores = verdict.get("scores") or {}
    ranked = sorted(orig, key=lambda oid: (-float(scores.get(oid, 0.0)), oid))  # по баллу, стабильно
    solo = len(live) < 2  # по факту судил один арбитр (честная пометка)
    tag = "solo" if solo else "council"
    best = []
    for oid in ranked[:keep]:
        o = orig[oid]
        if not isinstance(o, dict):
            best.append(o)
            continue
        card = dict(o, judged=tag)
        sc = scores.get(oid)
        if sc is not None:
            card["score"] = round(float(sc) * 10, 1)  # балл совета 0..1 → 0-10 для бейджа «оценка совета» (D6)
        best.append(card)
    return {
        "ideas_best": best,
        "council": {"live": live, "solo": solo, "woken": ("orchestra" in live), "why": verdict.get("why")},
    }


def _run_readability(inputs, env):
    """Редактор читаемости: карточкам-победителям (ideas_best) ставит балл читаемости и
    описание ниже порога переписывает самонесущим. Идею НЕ теряем, карточку НЕ выкидываем —
    правим только текст why. Живёт ПОСЛЕ отбора (чиним лишь то, что реально уйдёт в кучу) и
    ДО scrub (переписанный текст тоже проходит вычистку секретов). Без ключа — passthrough."""
    import wiring

    env = env if isinstance(env, dict) else {}
    e = {"min_score": float(env.get("read_min_score", 8))}  # порог 8 (режим «максимум качества»): ниже 8 → переписать
    llm = _content_llm(env)
    if llm:
        e["llm"] = llm
        # ОЦЕНКА читаемости — детерминированный суд: даём ей ОТДЕЛЬНЫЙ низкотемпературный вызов,
        # чтобы балл всегда парсился. temp 0.9 у ask — для генерации; рассуждающая модель на ней
        # изредка не отдавала чистый JSON scores → карточка проходила без правки (наблюдалось
        # живьём). score_llm строим ТОЛЬКО для ask_llm.ask (несёт kwarg temperature); чужой llm
        # (тест/stub) — score_llm нет, оценка падает на llm, поведение байт-в-байт как раньше.
        # Переписывание остаётся на llm (temp 0.9 — там живость нужна).
        import ask_llm  # локально: используется только тут, top-level dep не плодим

        if llm is ask_llm.ask:
            e["score_llm"] = lambda p: ask_llm.ask(p, temperature=0.2)
    if env.get("on_progress"):
        e["on_progress"] = env["on_progress"]  # живой суб-прогресс долетает до органа (иначе молчит)
    return wiring.readability_gate.run(inputs, e)


def _run_rank(inputs, env):
    import wiring

    env = env if isinstance(env, dict) else {}
    e = {"keep": 5}  # режим «максимум качества»: оставить топ-5 из 12 (жёсткий отбор ~40%, куча без потолка)
    llm = _content_llm(env)
    if llm:
        e["llm"] = llm
    if env.get("direction"):
        e["direction"] = env["direction"]  # судья-фолбэк тоже учитывает направление

    # Если все идеи - это болванки (LLM не работает / баланс 0 / оффлайн), то опрашивать совет
    # (оркестр/интуицию) бессмысленно и долго. Сразу переходим на быстрый оффлайн-отбор.
    ideas = (inputs or {}).get("ideas") or []
    all_stubs = len(ideas) > 0 and all(isinstance(i, dict) and i.get("brain") == "stub" for i in ideas)

    # СОВЕТ в живом цикле (гейт снят юзером 2026-07-13, ход Г): идеи судит взвешенный совет,
    # если есть 2-й живой голос (в env принесли цепочку интуиции / оркестр). Иначе — прежний
    # одиночный судья, офлайн байт-в-байт. Любой сбой совета -> тихий откат, конвейер не встаёт.
    if not all_stubs and env.get("council") is not False and (env.get("llm_chain") or env.get("orchestra")):
        try:
            out = _rank_by_council(inputs, env, keep=int(e["keep"]))
            if out is not None:
                return out
        except Exception:
            pass  # совет никогда не роняет отбор идей

    import council_config

    if not council_config.is_enabled("rank_ideas"):
        e.pop("llm", None)  # Если арбитр выключен явно, фолбэк строго оффлайн

    return wiring.rank_ideas.run(inputs, e)
