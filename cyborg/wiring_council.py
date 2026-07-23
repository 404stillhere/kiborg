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


def _anti_bland_scores(scores, breakdown):
    """A1 anti-bland: пересчёт итогового балла через breakdown (сырые голоса советников).

    Формула: final[oid] = 0.7 × weighted_avg + 0.3 × max(advisor scores for oid).

    Защищает ПОЛЯРИЗУЮЩИЕ идеи — где один советник дал высоко (max), остальные низко
    (низкое avg). Без anti-bland такие идеи тонули в середине (чистое avg из mind._tally).
    С anti-bland их «спасает» max-компонент (0.3) — идея, за которую горой стоит один
    советник, получает шанс против «серой» середнячковой.

    Источник данных — verdict["breakdown"] (mind.py:219-220): [{name, weight, scores:{oid:0..1}, why}].
    Веса берём ИЗ breakdown (исходные, не перенормированные) — как делает mind._tally, но
    локально здесь, чтобы не трогать замороженный mind.py. Без повторного LLM-вызова.

    Graceful degrade: breakdown пуст/мусор/нет нужного oid → возвращаем scores как есть.
    Поэтому существующие тесты с моками deliberate БЕЗ breakdown НЕ ломаются.
    """
    if not isinstance(breakdown, list) or not breakdown:
        return scores  # нет breakdown → ничего не пересчитываем (как было)
    _AVG_W, _MAX_W = 0.7, 0.3  # балл важнее, но max-компонент спасает поляризующие
    # собираем валидные голоса советников: [(weight, scores_dict)]
    voices = []
    for entry in breakdown:
        if not isinstance(entry, dict):
            continue
        w = entry.get("weight")
        sc = entry.get("scores")
        if not isinstance(w, (int, float)) or not isinstance(sc, dict):
            continue
        voices.append((float(w), sc))
    if not voices:
        return scores  # все записи мусор → не трогаем
    # нормируем веса к 1 (mind._live_weights делает то же для AVG-компонента)
    total_w = sum(w for w, _ in voices)
    if total_w <= 0:
        return scores
    # пересчёт по каждому oid из исходных scores
    out = {}
    for oid, base in scores.items():
        weighted_sum = 0.0
        max_score = 0.0
        found = False
        for w, sc in voices:
            v = sc.get(oid)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            weighted_sum += (w / total_w) * v
            if v > max_score:
                max_score = v
            found = True
        if not found:
            out[oid] = base  # oid нет в breakdown → оставляем исходный avg
        else:
            out[oid] = _AVG_W * weighted_sum + _MAX_W * max_score
    return out


def _mmr_select(candidates, orig, scores, keep):
    """A3 Maximal Marginal Relevance: greedy выбор keep идей из candidates.

    топ-1 по score; дальше на каждом шаге берём кандидата с max relevance:
        λ × score − (1−λ) × max_sim_to_selected
    λ=0.7 (балл важнее разнообразия, но не подавляет его). sim = Jaccard по значимым
    словам (title + why), переиспользуем _prov_tokens/_jaccard из wiring_ideate (та же
    семантика стоп-слов, что и для provenance — без предметно-общих «бот/система»).

    Цель: при равных/близких score не класть 3 идеи про одно и то же — разнообразие
    важнее +0.1 к баллу. Вырождается в чисто-балльный порядок при Jaccard=0 (однобуквенные
    title, разные темы) — поэтому существующие тесты с однобуквенными title НЕ ломаются.
    """
    from wiring_ideate import _jaccard, _prov_tokens

    _MMR_LAMBDA = 0.7  # 0.7 = балл важнее, но diversity имеет голос (0.3 × max_sim)
    if not candidates:
        return []
    # токены каждой идеи (один проход, не O(n²) парсингов)
    toks = {}
    for oid in candidates:
        o = orig.get(oid)
        if isinstance(o, dict):
            text = "%s %s" % (o.get("title", ""), o.get("why", ""))
        else:
            text = str(o)
        toks[oid] = _prov_tokens(text)
    selected = [candidates[0]]  # топ-1 по score (candidates уже отсортированы по баллу)
    remaining = set(candidates[1:])
    while remaining and len(selected) < keep:
        best_oid, best_rel = None, -1.0
        for oid in remaining:
            score = float(scores.get(oid, 0.0))
            max_sim = 0.0
            for soid in selected:
                sim = _jaccard(toks[oid], toks[soid])
                if sim > max_sim:
                    max_sim = sim
            rel = _MMR_LAMBDA * score - (1 - _MMR_LAMBDA) * max_sim
            if rel > best_rel:
                best_rel, best_oid = rel, oid
        selected.append(best_oid)
        remaining.discard(best_oid)
    return selected


def _deliberate_with_lazy_orchestra(question, options, context, env, orch, op, n_ideas):
    """A2: deliberate с опциональным двухфазным режимом lazy_orchestra + B2 scoped rebind weights.

    Канон (lazy_orchestra=False/отсутствует): один deliberate, orchestra в context безусловно
    (решение юзера 2026-07-13: «умный сомневается всегда»). Это путь по умолчанию — поведение
    как до A2, ничего не ломает.

    Lazy (env["lazy_orchestra"]=True и orchestra есть в env):
      Фаза 1: deliberate с context БЕЗ orchestra (только rank_ideas + ask_llm). Дешёво —
        оркестр (7 моделей × N идей) не зовётся.
      Фаза 2: если топ-3 rank_ideas и ask_llm расходятся (Jaccard overlap множеств топ-3 < 2/3)
        → повторный deliberate С orchestra для разрешения. Если согласны — Фаза 1 остаётся.
    Без orchestra в env (нет ключей) — Фаза 2 не запускается (оркестру не из чего голосовать).

    B2 scoped rebind: если council_weights.is_enabled() (Feedback Cortex активировал) →
    mind.WEIGHTS подменяется на council_weights.current_weights() в try/finally вокруг ВСЕХ
    deliberate-вызовов здесь. finally восстанавливает канон даже при исключении. mind.py
    (FROZEN) НЕ трогаем — WEIGHTS module-global, читается в момент deliberate.

    C4 shadow: на канон-пути (оркестр всегда) после deliberate замеряем overlap
    rank_ideas×ask_llm из breakdown и пишем в shadow_metrics.jsonl. Реальное поведение НЕ
    меняется — запись «что было бы если бы включили lazy». Данные для решения «включать ли A2».
    """
    keep = 3  # топ-3 для оценки согласия советников (ранг внутри breakdown)
    # B2: scoped rebind весов если Feedback Cortex включил адаптивный режим
    orig_weights = wiring.mind.WEIGHTS
    rebinded = False
    try:
        try:
            import council_weights

            if council_weights.is_enabled():
                wiring.mind.WEIGHTS = council_weights.current_weights()
                rebinded = True
        except Exception:
            pass  # council_weights недоступен/битый → канон, не роняем отбор
        if not env.get("lazy_orchestra") or not (isinstance(orch, dict) and orch.get("models")):
            # канон: оркестр всегда. Один deliberate С orchestra в context.
            verdict = wiring.mind.deliberate(question, options, _council_no_cap(context), context)
            # C4 shadow: замеряем «что было бы если бы lazy orchestra включили» — насколько
            # согласны rank_ideas×ask_llm на ТОМ ЖЕ breakdown (оркестр УЖЕ голосовал, мы только
            # читаем его副产品). Реальное поведение НЕ меняется: оркестр отработал, вердикт тот же.
            # Shadow-логирование живёт только когда orchestra был в context (есть что замерять) —
            # т.е. именно на канон-пути «оркестр всегда». Без orchestra overlap считать не из чего.
            _shadow_log_lazy(verdict, orch, n_ideas)
            return verdict

        # Фаза 1: deliberate БЕЗ orchestra
        ctx_no_orch = {k: v for k, v in context.items() if k != "orchestra"}
        if callable(op):
            op("совет судит %d идей (без оркестра — проверка согласия)" % n_ideas)
        verdict = wiring.mind.deliberate(question, options, _council_no_cap(ctx_no_orch), ctx_no_orch)

        # оценка согласия rank_ideas × ask_llm по топ-3 из breakdown
        breakdown = verdict.get("breakdown") or []
        top_rank = _top_k_from_breakdown(breakdown, "rank_ideas", keep)
        top_ask = _top_k_from_breakdown(breakdown, "ask_llm", keep)
        if top_rank is None or top_ask is None:
            return verdict  # кто-то из пары промолчал — нечего сверять, отдаём Фазу 1
        from wiring_ideate import _jaccard  # переиспользуем (та же семантика множеств)

        overlap = _jaccard(set(top_rank), set(top_ask))
        if overlap >= 2 / 3:
            return verdict  # согласны ≥ 2/3 → orchestra не нужен

        # Фаза 2: расхождение → повторный deliberate С orchestra для разрешения
        if callable(op):
            n_rev = len(orch["models"]) if isinstance(orch, dict) and orch.get("models") else 0
            op("советники разошлись — оркестр подключается%s" % (" (%d рецензентов)" % n_rev) if n_rev else "")
        return wiring.mind.deliberate(question, options, _council_no_cap(context), context)
    finally:
        if rebinded:
            wiring.mind.WEIGHTS = orig_weights  # восстанавливаем канон в ЛЮБОМ случае


def _top_k_from_breakdown(breakdown, name, k):
    """Топ-k id опций по баллам конкретного советника из breakdown. None если советника нет."""
    for entry in breakdown:
        if not isinstance(entry, dict) or entry.get("name") != name:
            continue
        sc = entry.get("scores")
        if not isinstance(sc, dict):
            return None
        ranked = sorted(sc.keys(), key=lambda oid: (-float(sc.get(oid, 0.0)), str(oid)))
        return ranked[:k]
    return None


def _shadow_log_lazy(verdict, orch, n_ideas):
    """C4: замерить overlap rank_ideas×ask_llm и записать в shadow_metrics.jsonl.

    НЕ меняет поведение — только наблюдатель. Читает breakdown уже свершившегося
    deliberate (оркестр голосовал в каноне), считает Jaccard топ-3 двух советников
    и пишет запись о том, «вызвал бы lazy orchestra Фазу 2 (расхождение) или нет».
    Любой сбой (нет breakdown, не-словарь, исключение в jsonl) → тихо, не роняем отбор.
    """
    try:
        breakdown = (verdict or {}).get("breakdown") or []
        if not isinstance(breakdown, list) or not breakdown:
            return
        keep = 3
        top_rank = _top_k_from_breakdown(breakdown, "rank_ideas", keep)
        top_ask = _top_k_from_breakdown(breakdown, "ask_llm", keep)
        if top_rank is None or top_ask is None:
            return  # кто-то промолчал — overlap считать не из чего, запись бесмысленна
        from wiring_ideate import _jaccard

        overlap = _jaccard(set(top_rank), set(top_ask))
        n_rev = len(orch["models"]) if isinstance(orch, dict) and orch.get("models") else None
        import shadow_metrics

        shadow_metrics.append(
            {
                "overlap": round(overlap, 3),
                "would_call_phase2": overlap < 2 / 3,
                "top_rank": top_rank,
                "top_ask": top_ask,
                "n_ideas": n_ideas,
                "n_reviewers": n_rev,
            }
        )
    except Exception:
        pass  # shadow — наблюдатель; НИКОГДА не роняет прогон


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
    # A2 LAZY ORCHESTRA (двухфазный, по умолчанию ВЫКЛ — флаг env["lazy_orchestra"]).
    # Режим «оркестр всегда» (решение юзера 2026-07-13: «умный сомневается всегда») — канон,
    # orchestra в context безусловно. Lazy resurrects идею «оркестр только при расхождении»
    # под shadow-флагом: Фаза 1 deliberate БЕЗ orchestra, Фаза 2 (с orchestra) только если
    # топ-3 rank_ideas×ask_llm расходится (Jaccard overlap < 2/3). Без оркестра в env — нечего
    # подключать, остаётся одна фаза.
    verdict = _deliberate_with_lazy_orchestra(question, options, context, env, orch, op, len(ideas))
    live = verdict.get("live") or []
    if verdict.get("degraded") or not live:  # никто не проголосовал -> плоский откат на судью
        return None
    # Арбитр внутри совета (mind.deliberate) УЖЕ отработал живой моделью (его опрашивают первым). Поэтому и
    # когда голос один (интуиция/оркестр промолчали), берём готовый результат ОТСЮДА, а не зовём
    # rank_ideas.run повторно — иначе второй платный вызов той же модели (нашёл скептик 2026-07-13).
    scores = verdict.get("scores") or {}
    # A1 ANTI-BLIND: пересчёт через breakdown (сырые голоса) — защищает поляризующие идеи
    # (0.7×avg + 0.3×max). Без breakdown (моки в тестах) → scores как есть, НЕ ломает существующее.
    scores = _anti_bland_scores(scores, verdict.get("breakdown"))
    ranked = sorted(orig, key=lambda oid: (-float(scores.get(oid, 0.0)), oid))  # по баллу, стабильно
    solo = len(live) < 2  # по факту судил один арбитр (честная пометка)
    tag = "solo" if solo else "council"
    # A4 DYNAMIC KEEP: не тупо топ-keep, а пороговый фильтр. Идея проходит если её балл
    # совета (0..1) >= 0.6 (=score 6.0 из 10 в бейдже) — «не хуже среднего». Слабые идеи
    # (>6.0 не дотянули) место не занимают: куча без потолка, фокус на сильнейших. Полы:
    # минимум 1 (если все слабые — лучшая по баллу, не пустота), потолок keep (по умолч.
    # 3). Кандидаты БЕЗ score (маловероятно в совете — голосовали все) трактуются как 0.0.
    _KEEP_MIN_SCORE = 0.6  # балл совета 0..1, ниже которого идея не проходит порог
    passing = [oid for oid in ranked if float(scores.get(oid, 0.0)) >= _KEEP_MIN_SCORE]
    if not passing:
        passing = ranked[:1]  # все слабые — отдаём лучшую по баллу, не пустоту (минимум 1)
    candidates = passing[:keep] if len(passing) <= keep else _mmr_select(passing, orig, scores, keep)
    picked = candidates if len(candidates) <= keep else candidates[:keep]
    best = []
    for oid in picked:
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
    e = {"keep": 3}  # оставить топ-3 из 8 (отбраковка 5, ~62%; куча без потолка, фокус на сильнейших)
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
