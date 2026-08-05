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
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from cyborg import config

PROMPT_TMPL = (
    "Ты генератор и технический аналитик проектных идей. На входе материалы с ID:\n"
    "внешние сигналы, карты проектов и доказательные фрагменты локальных файлов.\n"
    "Придумай {k} КОНКРЕТНЫХ идей (новый проект / аддон / скилл / улучшение системы).\n"
    "Оригинальность важна, но ДОКАЗАТЕЛЬНОСТЬ важнее красивой догадки.\n"
    "\n"
    "Для улучшения существующего проекта:\n"
    "- опирайся на карту, путь, номера строк и видимый код; не объявляй функцию отсутствующей,\n"
    "  если материалы этого не доказывают;\n"
    "- если данных недостаточно, честно назови идею гипотезой и предложи проверку;\n"
    "- укажи 1-3 source_ids, которые реально навели на идею;\n"
    "- verification — один конкретный способ доказать пользу после реализации.\n"
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
    '{{"title":"...","why":"...","effort":"легко|средне|тяжело",'
    '"source_ids":["..."],"verification":"..."}}\n'
    "Материалы:\n{items}\n"
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
        out.append(
            {
                "title": f"Идея по мотиву: {it.get('title', '')[: config.IDEATE_STUB_TITLE_MAX_CHARS]}",
                "why": "Заголовок наводит на смежный инструмент — проверить нишу.",
                "effort": _EFFORT[idx % 3],
                "brain": "stub",
                "source_ids": [str(it.get("id"))] if it.get("id") is not None else [],
                "verification": "Проверить гипотезу на маленьком прототипе.",
            }
        )
    return out


def _parse(raw, k):
    """Терпимо к формату модели: Gemini отдаёт pretty-printed МАССИВ, стенд-ин — JSONL.
    Пробуем: (1) весь ответ как JSON (массив/объект), (2) по объекту в строке,
    (3) выдрать {...}-блоки регуляркой. Иначе — пусто (вызыватель уйдёт на stub)."""
    raw = (raw or "").strip()
    objs = []
    try:  # 1) массив объектов (частый ответ Gemini)
        v = json.loads(raw)
        if isinstance(v, list):
            objs = [o for o in v if isinstance(o, dict)]
        elif isinstance(v, dict):
            # модель иногда оборачивает список: {"ideas":[...]} / {"result":[...]} — достаём
            # вложенный список идей, а НЕ считаем обёртку одной ПУСТОЙ карточкой (иначе 12
            # реальных идей внутри теряются, а непустой список из пустышки глушит фолбэк на stub)
            inner = next(
                (val for val in v.values() if isinstance(val, list) and any(isinstance(x, dict) for x in val)), None
            )
            objs = [o for o in inner if isinstance(o, dict)] if inner is not None else [v]
    except Exception:
        pass
    if not objs:  # 2) JSONL — по компактному объекту в строке
        for line in raw.splitlines():
            line = line.strip().rstrip(",")
            if line.startswith("{") and line.endswith("}"):
                try:
                    objs.append(json.loads(line))
                except Exception:
                    pass
    if not objs:  # 3) последний шанс — плоские {...}-блоки
        for m in re.findall(r"\{[^{}]*\}", raw, re.DOTALL):
            try:
                objs.append(json.loads(m))
            except Exception:
                pass
    out = []
    for o in objs:
        if isinstance(o, dict):
            source_ids = o.get("source_ids")
            if not isinstance(source_ids, list):
                source_ids = []
            source_ids = [
                str(value).strip()[: config.IDEATE_SOURCE_ID_MAX_CHARS]
                for value in source_ids
                if isinstance(value, (str, int)) and str(value).strip()
            ][: config.IDEATE_MAX_SOURCE_IDS]
            card = {
                "title": o.get("title", ""),
                "why": o.get("why", ""),
                "effort": o.get("effort", "средне"),
                "brain": "llm",
                "source_ids": source_ids,
            }
            verification = o.get("verification")
            if isinstance(verification, str) and verification.strip():
                card["verification"] = verification.strip()[: config.IDEATE_VERIFICATION_MAX_CHARS]
            out.append(card)
    return out[:k]


def _format_items(items):
    """Материалы для промпта.

    У файлов есть безопасный короткий context, у лент — только title. Источник
    ``self`` помечает свои элементы отдельно: только идеи, взятые из таких
    материалов, должны улучшать kiborg. Это не превращает весь смешанный прогон
    в глобальный режим самоулучшения.
    """
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        context = str(item.get("context") or "").strip()
        source_id = str(item.get("id") or "?").strip()
        role = str(item.get("role") or "").strip()
        source_tag = f"[SOURCE {source_id}]"
        if item.get("kind") == "self_reflection":
            row = (
                "- [САМОАНАЛИЗ KIBORG] "
                + ("[КАРТА ПРОЕКТА] " if item.get("project_map") else "")
                + source_tag
                + " "
                + title
                + "\n  Задача этого материала: предложить конкретное проверяемое улучшение самого kiborg."
            )
        elif item.get("project_map"):
            row = "- [КАРТА ПРОЕКТА] " + source_tag + " " + title
        else:
            row = "- " + source_tag + (f" [роль: {role}]" if role else "") + " " + title
        if context:
            row += "\n  Факты материала:\n    " + context.replace("\n", "\n    ")
        rows.append(row)
    return "\n".join(rows)


def run(inputs, env):
    env = env or {}
    inputs = inputs or {}
    items = inputs.get("items", [])
    k = int(env.get("k", config.DEFAULT_GEN_K))
    llm = env.get("llm")
    if callable(llm):
        op = env.get("on_progress")  # опц. живой суб-прогресс (один вызов, но ~5с — даём знать)
        if callable(op):
            op("генерирую %d идей" % k)
        prompt = PROMPT_TMPL.format(k=k, items=_format_items(items))
        direction = (env.get("direction") or "").strip()
        if direction:  # руль темы — впереди основного запроса
            prompt = _STEER_TMPL.format(direction=direction) + prompt
        rejected = [r for r in (env.get("rejected") or []) if r]
        if rejected:  # учёт отклонённого — «не приноси похожее на забракованное»
            prompt = _AVOID_TMPL.format(rejected="\n".join("- " + str(r) for r in rejected)) + prompt
        ideas = _parse(llm(prompt), k)
        if ideas:
            if direction:
                # Метаданные карточки: позднее видно, под каким рулём её придумали.
                # Это «запрошенное направление», а не обещание, что модель подчинилась идеально.
                for idea in ideas:
                    idea["direction"] = direction
            return {"ideas": ideas}
        # мозг не выдал парсибельного — честно падаем на stub
    ideas = _stub(items, k)
    direction = (env.get("direction") or "").strip()
    if direction:
        for idea in ideas:
            idea["direction"] = direction
    return {"ideas": ideas}


if __name__ == "__main__":
    print(json.dumps(run({"items": [{"title": "A tiny CRDT in 200 lines"}]}, {"k": 3}), ensure_ascii=False, indent=2))
