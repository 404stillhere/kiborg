"""Орган: ideate — из сырых items делает идеи-предложения с ценником.

Контракт: run(inputs, env) -> {"ideas": [{title, why, effort, brain}]}.
Два мозга:
  - env["llm"] = callable(prompt)->str  (в проде это ask_llm с ключом) — используем его,
    результат помечается brain="llm";
  - иначе stub-мозг: детерминированный, brain="stub" (доказывает трубы без ключа).
Ключ/сеть орган сам НЕ трогает — только через env["llm"].
Ценник (effort): «легко» / «средне» / «тяжело» — грубая оценка сил (это добавка Б).
"""
import json
import re

PROMPT_TMPL = (
    "Ты генератор проектных идей. На входе свежие внешние заголовки.\n"
    "Придумай {k} КОНКРЕТНЫХ идей (новый проект / аддон / скилл), которые они наводят,\n"
    "но оригинальных — не пересказ заголовка.\n"
    "\n"
    "Поле «why» — это ОПИСАНИЕ карточки, его читают БЕЗ всякого контекста. 4 правила:\n"
    "1. Самонесущесть. Начни с того, ЧТО это и ЧТО делает, простыми словами. НЕЛЬЗЯ\n"
    "   начинать с «на базе идеи…», «вдохновляясь…» и ссылок на то, чего в карточке нет.\n"
    "2. Сначала суть — потом термины. Одна ясная картинка, а не список умных слов.\n"
    "3. Кто субъект. Явно назови, кто действует и с кем/чем (кто нажимает — кто получает).\n"
    "4. Пример — только если он КОНКРЕТНЕЕ поясняемого слова; мутный пример убери.\n"
    "\n"
    "Плохо (висит в воздухе): «На базе идеи говорящего ошейника — ключевые звуки\n"
    "(щенка в пути, клич чужих)».\n"
    "Хорошо (читается с нуля): «Ошейник для собаки с микрофоном: распознаёт лай и шлёт\n"
    "хозяину на телефон, что это было — тревога, чужой у двери, скулёж».\n"
    "\n"
    "Каждую идею верни ОДНОЙ строкой JSON и ничего лишнего:\n"
    '{{"title":"...","why":"...","effort":"легко|средне|тяжело"}}\n'
    "Заголовки:\n{items}\n"
)

_EFFORT = ["легко", "средне", "тяжело"]

# Руль направления (env["direction"]): ставится ПЕРЕД основным запросом, чтобы модель
# гнула идеи в заданную тему, используя заголовки лишь как толчок. Пусто = без руля.
_STEER_TMPL = (
    "НАПРАВЛЕНИЕ (главное): придумывай идеи В СТОРОНУ темы «{direction}».\n"
    "Держись этого направления, даже если заголовки ниже про другое — бери их лишь\n"
    "как толчок, а саму идею гни в «{direction}».\n\n"
)

# Отклонённые идеи (env["rejected"]): юзер уже забраковал их «мусором». Ставим ПЕРЕД запросом,
# чтобы модель не приносила ни их, ни близкие вариации — учимся на отказах, не только на дедупе.
_AVOID_TMPL = (
    "НЕ ПРЕДЛАГАЙ идеи, похожие на эти УЖЕ ОТКЛОНЁННЫЕ (юзер их забраковал) — ни сами, ни\n"
    "близкие вариации той же сути:\n{rejected}\n\n"
)


def _stub(items, k):
    out = []
    for idx in range(k):
        it = items[idx % len(items)] if items else {"title": "—"}
        out.append({
            "title": f"Идея по мотиву: {it.get('title', '')[:60]}",
            "why": "Заголовок наводит на смежный инструмент — проверить нишу.",
            "effort": _EFFORT[idx % 3],
            "brain": "stub",
        })
    return out


def _parse(raw, k):
    """Терпимо к формату модели: Gemini отдаёт pretty-printed МАССИВ, стенд-ин — JSONL.
    Пробуем: (1) весь ответ как JSON (массив/объект), (2) по объекту в строке,
    (3) выдрать {...}-блоки регуляркой. Иначе — пусто (вызыватель уйдёт на stub)."""
    raw = (raw or "").strip()
    objs = []
    try:                                    # 1) массив объектов (частый ответ Gemini)
        v = json.loads(raw)
        if isinstance(v, list):
            objs = [o for o in v if isinstance(o, dict)]
        elif isinstance(v, dict):
            # модель иногда оборачивает список: {"ideas":[...]} / {"result":[...]} — достаём
            # вложенный список идей, а НЕ считаем обёртку одной ПУСТОЙ карточкой (иначе 12
            # реальных идей внутри теряются, а непустой список из пустышки глушит фолбэк на stub)
            inner = next((val for val in v.values()
                          if isinstance(val, list) and any(isinstance(x, dict) for x in val)), None)
            objs = [o for o in inner if isinstance(o, dict)] if inner is not None else [v]
    except Exception:
        pass
    if not objs:                            # 2) JSONL — по компактному объекту в строке
        for line in raw.splitlines():
            line = line.strip().rstrip(",")
            if line.startswith("{") and line.endswith("}"):
                try:
                    objs.append(json.loads(line))
                except Exception:
                    pass
    if not objs:                            # 3) последний шанс — плоские {...}-блоки
        for m in re.findall(r"\{[^{}]*\}", raw, re.DOTALL):
            try:
                objs.append(json.loads(m))
            except Exception:
                pass
    out = []
    for o in objs:
        if isinstance(o, dict):
            out.append({
                "title": o.get("title", ""),
                "why": o.get("why", ""),
                "effort": o.get("effort", "средне"),
                "brain": "llm",
            })
    return out[:k]


def run(inputs, env):
    env = env or {}
    inputs = inputs or {}
    items = inputs.get("items", [])
    k = int(env.get("k", 3))
    llm = env.get("llm")
    if callable(llm):
        op = env.get("on_progress")          # опц. живой суб-прогресс (один вызов, но ~5с — даём знать)
        if callable(op):
            op("генерирую %d идей" % k)
        prompt = PROMPT_TMPL.format(k=k, items="\n".join("- " + i.get("title", "") for i in items))
        direction = (env.get("direction") or "").strip()
        if direction:                       # руль темы — впереди основного запроса
            prompt = _STEER_TMPL.format(direction=direction) + prompt
        rejected = [r for r in (env.get("rejected") or []) if r]
        if rejected:                        # учёт отклонённого — «не приноси похожее на забракованное»
            prompt = _AVOID_TMPL.format(rejected="\n".join("- " + str(r) for r in rejected)) + prompt
        ideas = _parse(llm(prompt), k)
        if ideas:
            return {"ideas": ideas}
        # мозг не выдал парсибельного — честно падаем на stub
    return {"ideas": _stub(items, k)}


if __name__ == "__main__":
    print(json.dumps(run({"items": [{"title": "A tiny CRDT in 200 lines"}]}, {"k": 3}),
                      ensure_ascii=False, indent=2))
