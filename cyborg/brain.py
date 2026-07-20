"""Мозг — планировщик: решает следующий шаг цикла. Два режима (как ideate):
  - env['llm'] = callable(prompt)->str: LLM-планировщик (в проде — ask_llm с ключом);
  - иначе Stub: детерминированный дата-флоу планировщик (без ключа, но реально работает).
Возвращает {'action':'call'|'finish', 'organ':Organ|None, 'inputs':dict, 'why':str}.
"""

import json

# что цель хочет на выходе -> ключ памяти-результат
_DELIVERABLE = [
    (("идея", "идеи", "идей", "предлож"), "ideas"),
    (("доделать", "существу", "финиш", "доведи"), "nudge"),
    (("новост", "собрать", "источник", "сырь"), "items"),
]


def _terminal(seed, organs):
    """Пройти по графу дата-флоу вниз: пока какой-то орган ПОТРЕБЛЯЕТ текущий ключ,
    перейти к тому, что он ПРОИЗВОДИТ. Возвращает конец цепочки (терминальный ключ).
    Так «идеи» превращаются в «delivered», если есть sink-доставка; без неё — остаются «идеи».
    """
    seen = set()
    cur = seed
    while cur and cur not in seen:
        seen.add(cur)
        nxt = None
        for o in organs:
            if cur in o.consumes and o.produces:
                nxt = o.produces[0]
                break
        if nxt is None:
            break
        cur = nxt
    return cur


def infer_deliverable(goal, organs):
    g = (goal or "").lower()
    seed = None
    for words, key in _DELIVERABLE:
        if any(w in g for w in words):
            seed = key
            break
    if seed is None:
        for o in organs:
            if o.produces:
                seed = o.produces[0]
                break
    return _terminal(seed, organs)


def _runnable(o, mem_keys, memory):
    if o.name in memory.blocked:
        return False
    # входы должны быть НЕПУСТЫ (mem_keys = ключи с непустым значением)
    if not all(c in mem_keys for c in o.consumes):
        return False
    # орган «не отработал», только если СВОЙ produces-ключ ещё НЕ записан (даже пустым).
    # produced (а не has-непустое) рвёт холостой спин: пустой items -> ключ записан -> не переизбираем.
    return (not o.produces) or any(p not in memory.produced for p in o.produces)


def stub_plan(goal, candidates, memory, deliverable):
    if deliverable and memory.has(deliverable):
        return {"action": "finish", "organ": None, "inputs": {}, "why": "цель достигнута: " + deliverable}
    mem_keys = set(k for k in memory.data if memory.has(k))
    # приоритет: (1) кто производит целевой ключ — вперёд; (2) меньше зависимостей;
    # (3) источники раньше трансформеров. Так киборг целится в результат, а не гоняет лишнее.
    order = sorted(
        candidates,
        key=lambda o: (
            0 if deliverable in o.produces else 1,
            len(o.consumes),
            0 if o.role == "source" else 1,
        ),
    )
    for o in order:
        if _runnable(o, mem_keys, memory):
            inputs = {c: memory.data[c] for c in o.consumes}
            return {"action": "call", "organ": o, "inputs": inputs, "why": f"{o.role}:{o.name} -> {o.produces}"}
    return {"action": "finish", "organ": None, "inputs": {}, "why": "нет применимого органа"}


def llm_plan(goal, candidates, memory, deliverable, llm):
    lines = [
        "Цель: " + goal,
        "Нужен ключ-результат: " + str(deliverable),
        "Уже в памяти: " + (", ".join(memory.data.keys()) or "(пусто)"),
        "Доступные органы:",
    ]
    for i, o in enumerate(candidates):
        lines.append(f"{i}. {o.name} — {o.purpose[:80]} [consumes={o.consumes} produces={o.produces}]")
    lines.append('Ответь ОДНОЙ строкой JSON: {"index": N} чтобы вызвать орган N, или {"finish": true}.')
    raw = llm("\n".join(lines))
    mem_keys = set(k for k in memory.data if memory.has(k))
    for ln in (raw or "").splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("finish"):
            return {"action": "finish", "organ": None, "inputs": {}, "why": "llm: finish"}
        try:
            org = candidates[int(o.get("index"))]
        except Exception:
            continue
        # мозг-LLM мог выбрать неготовый орган (вход ещё не произведён) или заблокированный —
        # тот же guard, что и у stub, чтобы LLM не запустил трансформер с inputs={key:None}
        if not _runnable(org, mem_keys, memory):
            continue
        inputs = {c: memory.data[c] for c in org.consumes}
        return {"action": "call", "organ": org, "inputs": inputs, "why": "llm: " + org.name}
    return None  # не распарсил / выбор неготов -> наверх упадём на stub


def plan(goal, candidates, memory, env, organs_all=None):
    deliverable = infer_deliverable(goal, organs_all or candidates)
    llm = env.get("llm") if isinstance(env, dict) else None
    if callable(llm):
        res = llm_plan(goal, candidates, memory, deliverable, llm)
        if res is not None:
            return res
    return stub_plan(goal, candidates, memory, deliverable)
