"""Орган: readability_gate — «редактор читаемости». Проверяет, что ОПИСАНИЕ (why) каждой
готовой карточки читается БЕЗ внешнего контекста, и чинит те, что не дотягивают.

Зачем: генератор (ideate) с обновлённым промптом обычно пишет читаемо, но изредка выдаёт
мутную карточку (ссылка на отсутствующее, термины вместо картинки). Отдельный ОЦЕНОЧНЫЙ проход
(тот же принцип, что у rank_ideas — генератор не судит себя сам; но тут судим ЧИТАЕМОСТЬ, а не
пользу) ставит каждому why балл 0-10 и НИЖЕ ПОРОГА переписывает ТОЛЬКО описание самонесущим.
Переписанное СРАВНИВАЕТ со старым в ОДНОМ вызове судьи (пара old|new — иначе несравнимо:
батчевый и сольный балл судья калибрует по-разному) и берёт правку ТОЛЬКО если новый текст
строго лучше И реально отличается — слабый rewrite не уходит хуже исходного. Идею не теряем,
карточку не выкидываем, количество не меняем — правим текст на месте.

Контракт: run(inputs, env) -> {"ideas_polished": [...]}.
  env["llm"] — редактор (переписывание) callable(prompt)->str; env["min_score"] — порог (default 7).
  env["score_llm"] — ОТДЕЛЬНЫЙ судья балла (опционально): низкотемпературный вызов, чтобы оценка
    всегда парсилась. Нет его — судим тем же env["llm"] (прежнее поведение).
Без llm / при сбое парса — passthrough (карточки как есть). ВСЕГДА производит ideas_polished
(даже без правок), иначе конвейер встанет на следующем звене (scrub его потребляет).
Ключ/сеть орган сам НЕ трогает — только через env["llm"]/env["score_llm"].
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
    # ОЦЕНКУ балла судим ВЫДЕЛЕННЫМ низкотемпературным вызовом (score_llm), если дали: temp
    # генератора (0.9) заставляла рассуждающую модель изредка не отдавать чистый JSON scores →
    # карточка проходила без правки. Переписывание остаётся на llm (там нужна живость). Нет
    # score_llm — судим тем же llm (прежнее поведение, тесты/чужой llm не трогаем).
    judge = env.get("score_llm") if callable(env.get("score_llm")) else llm
    scores = _score(judge, ideas)
    if scores is None:
        scores = _score(judge, ideas)          # один повтор: судья изредка шумит на первом заходе
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
                title = card.get("title", "")
                old_why = card.get("why", "")
                new_why = _rewrite(llm, title, old_why)
                if new_why and new_why != old_why:
                    # РЕ-ОЦЕНКА: старый и новый текст судим В ОДНОМ вызове (пара old|new), иначе
                    # несравнимо — батчевый sc и сольный балл судья калибрует по-разному (по
                    # соседям в промпте), и сольная оценка систематически выше. Правку берём ТОЛЬКО
                    # если новый СТРОГО лучше старого В ТЕХ ЖЕ УСЛОВИЯХ. +1 повтор на шум/частичный
                    # парс. Не лучше → откат на старое (карточка не должна стать хуже).
                    pair = [{"title": title, "why": old_why}, {"title": title, "why": new_why}]
                    ps = _score(judge, pair)
                    if not ps or len(ps) < 2 or ps[0] is None or ps[1] is None:
                        ps = _score(judge, pair)
                    old_s = ps[0] if (ps and len(ps) > 0) else None
                    new_s = ps[1] if (ps and len(ps) > 1) else None
                    if old_s is not None and new_s is not None and new_s > old_s:
                        card["why"] = new_why
                        card["read_fixed"] = True
                        card["read_score"] = round(new_s, 1)   # балл финального текста (в паре)
        out.append(card)
    return {"ideas_polished": out}


if __name__ == "__main__":
    NEW = "Ошейник для собаки с микрофоном: распознаёт лай и шлёт хозяину, что это было."
    def fake(p):
        # батч [c0,c1]: c0 мутная (3) -> перепишем; пара [old,new]: new (9) > old (3) -> правку берём
        if '"scores"' in p:
            return '{"scores":[3,9]}'
        return '{"why":"%s"}' % NEW
    demo = [{"title": "BarkTalk", "why": "На базе идеи говорящего ошейника — ключевые звуки (щенка в пути)"},
            {"title": "PayWhenEarn", "why": "Платишь только когда заработал — простая понятная схема без аванса"}]
    print(json.dumps(run({"ideas_best": demo}, {"llm": fake, "min_score": 7}),
                      ensure_ascii=False, indent=2))
