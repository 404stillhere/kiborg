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
    "но оригинальных — не пересказ заголовка. Каждую верни ОДНОЙ строкой JSON:\n"
    '{{"title":"...","why":"...","effort":"легко|средне|тяжело"}}\n'
    "Заголовки:\n{items}\n"
)

_EFFORT = ["легко", "средне", "тяжело"]


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
            objs = [v]
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
        prompt = PROMPT_TMPL.format(k=k, items="\n".join("- " + i.get("title", "") for i in items))
        ideas = _parse(llm(prompt), k)
        if ideas:
            return {"ideas": ideas}
        # мозг не выдал парсибельного — честно падаем на stub
    return {"ideas": _stub(items, k)}


if __name__ == "__main__":
    print(json.dumps(run({"items": [{"title": "A tiny CRDT in 200 lines"}]}, {"k": 3}),
                      ensure_ascii=False, indent=2))
