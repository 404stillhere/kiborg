# fmt: off
# Замороженное ядро (гейт человека, см. README): движок совета. Black/ruff НЕ форматируют
# этот файл — стабильность важнее единообразия стиля. Маркер # fmt: off — документированная
# гарантия black. Правит параллельная сессия; cosmetically-реформатting может конфликтовать.
"""Мыслящая часть Мозга — ВЗВЕШЕННОЕ СОВЕЩАНИЕ (deliberation).

Отдельный слой суждения НАД планировщиком (brain.py). Планировщик решает «какой
орган вызвать следующим» (дёшево, детерминированно, надёжно для линейного конвейера).
Мыслящая часть решает другое: «из этих кандидатов какой лучше / какой вердикт» —
и делает это не одним голосом, а СОВЕТОМ из трёх советников с разным весом важности.

Три советника (площадка — advisors.py):
  ask_llm    (вес 0.39) — интуиция: одна модель из цепочки-с-фолбэком (DarBench organ.js).
  orchestra  (вес 0.20) — совет: N независимых моделей-рецензентов, свод (Dual Mode organ.py).
  rank_ideas (вес 0.41) — арбитр: строгий отбор по рубрике (уже живой орган киборга).

Формула. Каждый советник, если он ПРИМЕНИМ и ЖИВ, ставит каждому варианту балл [0..1].
Итог варианта = Σ вес_советника · балл_советника(вариант), причём веса берутся ТОЛЬКО
по живым советникам и перенормируются к 1 (см. _live_weights). Побеждает max.

Автономность (главное). Киборг должен думать и без Claude, и без части ключей. Поэтому:
любой советник, который не подключён / упал / без ключа / не к месту — ВОЗДЕРЖИВАЕТСЯ
(opine -> None), его вес перетекает на оставшихся. Если воздержались ВСЕ — совещание
честно возвращает choice=None и degraded=True: вызыватель уходит на детерминированный
фолбэк (тот же stub_plan, что и сейчас). Совет никогда не роняет цикл.

 Core.py не трогается; brain.py не трогается. Это ПЛОЩАДКА — подключение в живой цикл
решает юзер (гейт), см. .brain/design/mind-council.md.
"""

# Веса важности мнений (задано юзером 2026-07-13, в порядке ask_llm/orchestra/rank_ideas).
# Сумма = 1.0. Меняются здесь и только здесь — единственный источник истины.
WEIGHTS = {
    "ask_llm": 0.39,
    "orchestra": 0.20,
    "rank_ideas": 0.41,
}

# При равном итоговом балле — чей голос перевешивает (по убыванию веса). Затем — порядок
# вариантов (стабильно, детерминированно: одинаковый вход -> одинаковый выбор).
_TIE_ORDER = ["rank_ideas", "ask_llm", "orchestra"]


def opinion(scores, confidence=1.0, rationale="", raw=None, escalate=False):
    """Нормализованное МНЕНИЕ советника — единый формат, к которому площадка приводит
    разные выходы (текст ask_llm, вердикты orchestra, ранжирование rank_ideas).

    scores      — {option_id: балл 0..1}. Вариант без ключа считается баллом 0.
    confidence  — 0..1, насколько советник уверен (хук на будущее; в базовой формуле
                  не домножается — веса статические, «важность», а не «уверенность»).
    rationale   — короткое «почему» для журнала/пульта.
    raw         — сырой ответ советника (для аудита, не для логики).
    escalate    — только у ИНТУИЦИИ: True = «я не уверена, позовите совет» (см. think()).
    """
    clean = {}
    for k, v in (scores or {}).items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f:                      # NaN (nan<0 и nan>1 оба False — иначе просочился бы в else)
            continue                    # модель могла вернуть NaN (json.loads allow_nan) — отбрасываем
        clean[k] = 0.0 if f < 0 else 1.0 if f > 1 else f
    return {"scores": clean, "confidence": float(confidence), "rationale": str(rationale),
            "raw": raw, "escalate": bool(escalate)}


def _live_weights(live_names):
    """Веса ТОЛЬКО живых советников, перенормированные к сумме 1. Пусто -> {}."""
    total = sum(WEIGHTS.get(n, 0.0) for n in live_names)
    if total <= 0:
        return {}
    return {n: WEIGHTS.get(n, 0.0) / total for n in live_names}


def _tally(votes, ids):
    """Свести голоса в итог. votes = {name: {id: score}} — ТОЛЬКО живые известные советники.
    Возвращает (final:{id:score}, best_id, live_weights). Пусто -> ({}, None, {}).
    Единая формула для deliberate (плоский) и think (иерархия) — считают одинаково."""
    live = list(votes.keys())
    if not live or not ids:
        return {}, None, {}
    lw = _live_weights(live)
    final = {oid: 0.0 for oid in ids}
    for name in live:
        w, sc = lw[name], votes[name]
        for oid in ids:
            final[oid] += w * float(sc.get(oid, 0.0))   # нет балла на вариант = 0

    def _tie_key(oid):                                   # тай-брейк: старший по весу голос, затем порядок
        by_senior = [(-float(votes.get(tn, {}).get(oid, 0.0)) if tn in live else 0.0) for tn in _TIE_ORDER]
        return (-final[oid], *by_senior, ids.index(oid))

    return final, sorted(ids, key=_tie_key)[0], lw


def _norm_options(options):
    """Варианты -> (opts, ids). Не-dict оборачиваем; проставляем id; ДУБЛИ id схлопываем
    (первый выигрывает) — иначе один вариант учёлся бы дважды и перевесил бы честного лидера."""
    opts, ids = [], []
    for i, o in enumerate(options or []):
        o = dict(o) if isinstance(o, dict) else {"id": i, "value": o}
        o.setdefault("id", i)
        if o["id"] in ids:
            continue
        ids.append(o["id"])
        opts.append(o)
    return opts, ids


def _ask_advisor(adv, name, question, opts, context):
    """Опросить одного советника. -> (opinion|None, reason). Чужое имя/падение/воздержание
    = (None, причина); цикл никогда не роняется исключением советника."""
    if name not in WEIGHTS:                       # советник без веса — не знаем, как учесть голос
        return None, "no weight in WEIGHTS"
    try:
        op = adv.opine(question, opts, context or {})
    except Exception as e:                        # советник упал — воздержание, цикл живёт
        return None, "error: " + str(e)[:120]
    if not op or not op.get("scores"):
        return None, "abstained"
    return op, None


def _verdict(opts, ids, votes, breakdown, abstained, extra=None):
    """Собрать Verdict из голосов через общий _tally. votes пуст -> degraded (фолбэк вызывателя)."""
    if not votes:
        v = {"choice": None, "choice_id": None, "scores": {}, "breakdown": breakdown,
             "live": [], "abstained": abstained, "degraded": True,
             "why": "все советники воздержались — решение отдано детерминированному фолбэку"}
        return {**v, **(extra or {})}
    final, best_id, lw = _tally(votes, ids)
    choice = next((o for o in opts if o["id"] == best_id), None)
    live_str = ", ".join(f"{n}·{lw[n]:.2f}" for n in sorted(votes, key=lambda n: -WEIGHTS.get(n, 0.0)))
    v = {"choice": choice, "choice_id": best_id, "scores": final, "breakdown": breakdown,
         "live": list(votes.keys()), "abstained": abstained, "degraded": False,
         "why": f"выбран {best_id} (итог {final[best_id]:.3f}); голосовали: {live_str}"}
    return {**v, **(extra or {})}


def think(question, options, council, context=None):
    """ИЕРАРХИЧЕСКОЕ мышление (виденье юзера 2026-07-13):

      арбитр (rank_ideas) судит ВСЕГДА · интуиция (ask_llm) думает ВСЕГДА и САМА решает,
      звать ли совет (orchestra) · совет просыпается ТОЛЬКО когда интуиция не уверена.

    Так обычное решение стоит двух голосов (арбитр 0.41 + интуиция 0.39, нормировка к 1),
    а дорогой совет (0.20) включается лишь при сомнении интуиции — экономнее, чем звать всех.

    council — те же советники, что и для deliberate (по .name). Verdict как у deliberate плюс:
      council_woken — будили ли совет; escalate — подняла ли интуиция флаг сомнения.
    """
    by_name = {getattr(a, "name", "?"): a for a in (council or [])}
    opts, ids = _norm_options(options)
    if not opts:
        return {"choice": None, "choice_id": None, "scores": {}, "breakdown": [], "live": [],
                "abstained": [], "degraded": True, "council_woken": False, "escalate": False,
                "why": "нет вариантов для совещания"}

    votes, breakdown, abstained = {}, [], []

    def _collect(name):
        adv = by_name.get(name)
        if adv is None:
            return None
        op, reason = _ask_advisor(adv, name, question, opts, context)
        if op is None:
            abstained.append({"name": name, "reason": reason})
            return None
        votes[name] = op["scores"]
        breakdown.append({"name": name, "weight": WEIGHTS.get(name, 0.0),
                          "scores": op["scores"], "why": op.get("rationale", "")})
        return op

    _collect("rank_ideas")                        # арбитр — всегда
    op_int = _collect("ask_llm")                   # интуиция — всегда
    # совет — ТОЛЬКО если интуиция жива И подняла флаг «не уверена»
    escalate = bool(op_int and op_int.get("escalate"))
    council_woken = False
    if escalate and "orchestra" in by_name:
        if _collect("orchestra") is not None:
            council_woken = True

    return _verdict(opts, ids, votes, breakdown, abstained,
                    extra={"council_woken": council_woken, "escalate": escalate})


def deliberate(question, options, council, context=None):
    """Провести совещание. Вернуть Verdict.

    question — что решаем (строка, идёт советникам в промпт).
    options  — список вариантов; каждый dict с полем 'id' (или получит индекс).
    council  — список советников (advisors.py): объект с .name, .opine(question, options, context)
               -> opinion()|None. None = воздержался (не подключён/упал/не к месту).
    context  — произвольный словарь (память, цель, env) для советников.

    Verdict = {
      choice: option|None,        — победивший вариант (None если совет пуст)
      choice_id, scores,          — id победителя и итоговые баллы по вариантам
      breakdown,                  — вклад каждого живого советника (для пульта/аудита)
      live, abstained,            — кто голосовал, кто воздержался
      degraded,                   — True если ни один советник не проголосовал
      why,                        — человекочитаемое объяснение
    }
    """
    opts, ids = _norm_options(options)
    if not opts:                         # нет вариантов — совещать нечего, честно деградируем
        return {"choice": None, "choice_id": None, "scores": {}, "breakdown": [], "live": [],
                "abstained": [], "degraded": True, "why": "нет вариантов для совещания"}

    votes, breakdown, abstained = {}, [], []
    for adv in (council or []):
        name = getattr(adv, "name", "?")
        op, reason = _ask_advisor(adv, name, question, opts, context)
        if op is None:
            abstained.append({"name": name, "reason": reason})
            continue
        votes[name] = op["scores"]
        breakdown.append({"name": name, "weight": WEIGHTS.get(name, 0.0),
                          "scores": op["scores"], "why": op.get("rationale", "")})

    return _verdict(opts, ids, votes, breakdown, abstained)


if __name__ == "__main__":
    # Смоук без сети/ключей: три фейковых советника, проверяем взвешивание и деградацию.
    class _Fake:
        def __init__(self, name, table):
            self.name = name
            self._t = table
        def opine(self, q, opts, ctx):
            return opinion(self._t) if self._t else None

    opts = [{"id": "A", "title": "идея A"}, {"id": "B", "title": "идея B"}]
    # rank_ideas(0.41) за B, ask_llm(0.39) за A, orchestra(0.20) за A
    council = [
        _Fake("ask_llm", {"A": 1.0, "B": 0.0}),
        _Fake("orchestra", {"A": 0.8, "B": 0.2}),
        _Fake("rank_ideas", {"A": 0.0, "B": 1.0}),
    ]
    v = deliberate("какая идея лучше?", opts, council)
    # A: 0.39*1 + 0.20*0.8 + 0 = 0.55 ; B: 0 + 0.20*0.2 + 0.41*1 = 0.45 -> A
    print("full council ->", v["choice_id"], "| scores:", {k: round(x, 3) for k, x in v["scores"].items()})

    # orchestra выпал (нет ключа) — веса ask_llm/rank_ideas перенормированы к 1
    council2 = [
        _Fake("ask_llm", {"A": 1.0, "B": 0.0}),
        _Fake("orchestra", None),
        _Fake("rank_ideas", {"A": 0.0, "B": 1.0}),
    ]
    v2 = deliberate("какая идея лучше?", opts, council2)
    # live weights: ask_llm 0.39/0.80=0.4875, rank 0.41/0.80=0.5125 -> B выигрывает
    print("orchestra down ->", v2["choice_id"], "| live:", v2["live"])

    # все воздержались — degraded, фолбэк вызывателя
    v3 = deliberate("q", opts, [_Fake("ask_llm", None), _Fake("rank_ideas", None)])
    print("all down ->", "degraded" if v3["degraded"] else "??", "| choice:", v3["choice_id"])
