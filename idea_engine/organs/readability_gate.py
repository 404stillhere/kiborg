"""Орган: readability_gate — «редактор читаемости». Проверяет, что ОПИСАНИЕ (why) каждой
готовой карточки читается БЕЗ внешнего контекста, и чинит те, что не дотягивают.

Зачем: генератор (ideate) с обновлённым промптом обычно пишет читаемо, но изредка выдаёт
мутную карточку (ссылка на отсутствующее, термины вместо картинки). Отдельный ОЦЕНОЧНЫЙ проход
(тот же принцип, что у rank_ideas — генератор не судит себя сам; но тут судим ЧИТАЕМОСТЬ, а не
пользу) ставит каждому why балл 0-10 и НИЖЕ ПОРОГА переписывает ТОЛЬКО описание самонесущим.
Идею не теряем, карточку не выкидываем, количество не меняем — правим текст на месте.

Контракт: run(inputs, env) -> {"ideas_polished": [...]}.
  env["llm"] — судья/редактор callable(prompt)->str; env["min_score"] — порог (default 7).
Без llm / при сбое парса — passthrough (карточки как есть). ВСЕГДА производит ideas_polished
(даже без правок), иначе конвейер встанет на следующем звене (scrub его потребляет).
Ключ/сеть орган сам НЕ трогает — только через env["llm"].
"""
import json
import re

SCORE_TMPL = (
    "Ты редактор ЧИТАЕМОСТИ. Ниже {n} карточек идей (заголовок + описание why).\n"
    "Оцени КАЖДОЕ описание: читается ли оно С НУЛЯ, без внешнего контекста.\n"
    "Критерии: самонесущесть (не ссылается на то, чего в карточке нет), сначала суть —\n"
    "потом термины, ясный субъект (кто действует и с кем), пример конкретнее поясняемого слова.\n"
    "Верни ОДНУ строку JSON и ничего больше: {{\"scores\":[b0,b1,...]}} — балл 0-10 каждой\n"
    "карточке ПО ПОРЯДКУ (10 = кристально ясно с нуля).\n"
    "Карточки:\n{items}\n"
)

REWRITE_TMPL = (
    "Перепиши ОПИСАНИЕ идеи так, чтобы оно читалось С НУЛЯ, без всякого контекста.\n"
    "Правила: начни с того, ЧТО это и ЧТО делает, простыми словами; НЕ начинай с «на базе\n"
    "идеи…» или ссылок на то, чего тут нет; сначала суть — потом термины; назови субъекта\n"
    "(кто действует и с кем); пример — только если он конкретнее поясняемого слова. 1-3\n"
    "предложения, по-русски. Саму идею не меняй — только сделай описание понятным.\n"
    "Заголовок: {title}\n"
    "Старое описание: {why}\n"
    "Верни ОДНУ строку JSON и ничего больше: {{\"why\":\"...\"}}\n"
)


def _score(llm, ideas):
    """Балл читаемости каждой карточке (по порядку). None -> не смогли распарсить (наверху
    passthrough без правок). Терпимо к формату: цельный JSON / выдрать scores:[...] регуляркой."""
    items = "\n".join(f"{i}. {d.get('title', '')} — {d.get('why', '')}"
                      for i, d in enumerate(ideas) if isinstance(d, dict))
    raw = (llm(SCORE_TMPL.format(n=len(ideas), items=items)) or "").strip()
    arr = None
    for cand in [raw] + raw.splitlines():
        cand = cand.strip()
        if '"scores"' not in cand:
            continue
        try:
            o = json.loads(cand)
            if isinstance(o.get("scores"), list):
                arr = o["scores"]
                break
        except Exception:
            continue
    if arr is None:                              # последний шанс — числа из scores:[...]
        m = re.search(r'"scores"\s*:\s*\[([0-9.,\s]+)\]', raw)
        if m:
            arr = [float(x) for x in re.findall(r"[0-9.]+", m.group(1))]
    if not arr:
        return None
    out = []
    for v in arr:
        try:
            out.append(max(0.0, min(10.0, float(v))))
        except Exception:
            out.append(None)
    return out


def _rewrite(llm, title, why):
    """Переписанное самонесущее описание, либо None (тогда наверху остаётся старое)."""
    raw = (llm(REWRITE_TMPL.format(title=title, why=why)) or "").strip()
    for cand in [raw] + raw.splitlines():
        cand = cand.strip()
        if '"why"' not in cand:
            continue
        try:
            o = json.loads(cand)
            w = o.get("why")
            if isinstance(w, str) and w.strip():
                return w.strip()
        except Exception:
            continue
    return None


def run(inputs, env):
    env = env or {}
    inp = inputs or {}
    ideas = list(inp.get("ideas_best") or inp.get("ideas") or [])
    min_score = float(env.get("min_score", 7))
    llm = env.get("llm")
    if not callable(llm) or not ideas:
        return {"ideas_polished": ideas}       # без модели/идей — конвейер продолжается как есть
    scores = _score(llm, ideas)
    out = []
    for i, idea in enumerate(ideas):
        if not isinstance(idea, dict):
            out.append(idea)
            continue
        card = dict(idea)
        sc = scores[i] if (scores and i < len(scores)) else None
        if sc is not None:
            card["read_score"] = round(sc, 1)
            if sc < min_score:
                new_why = _rewrite(llm, card.get("title", ""), card.get("why", ""))
                if new_why:
                    card["why"] = new_why
                    card["read_fixed"] = True
        out.append(card)
    return {"ideas_polished": out}


if __name__ == "__main__":
    fake = lambda p: ('{"scores":[3,9]}' if '"scores"' in p
                      else '{"why":"Ошейник для собаки с микрофоном: распознаёт лай и шлёт хозяину, что это было."}')
    demo = [{"title": "BarkTalk", "why": "На базе идеи говорящего ошейника — ключевые звуки (щенка в пути)"},
            {"title": "PayWhenEarn", "why": "Платишь только когда заработал — простая понятная схема без аванса"}]
    print(json.dumps(run({"ideas_best": demo}, {"llm": fake, "min_score": 7}),
                      ensure_ascii=False, indent=2))
