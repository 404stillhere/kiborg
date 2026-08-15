"""Орган слияния: отобранные материалы → ОДНА «ультра-идея» (кросс-источниковый синтез).

Анти-Франкенштейн стоит на трёх опорах (обоснование — council 2026-08-15):
  1. РОЛИ. Каждый материал обязан занять СВОЙ слот каркаса (кто/механизм/поверхность/
     ограничение/сигнал). Коллаж «фича А + фича Б» = два материала в одном слоте →
     запрещён не уговорами, а схемой.
  2. ПОРЯДОК ПОЛЕЙ. digest → frame → title → … JSON генерируется слева направо, поэтому
     разбор сути идёт ДО заголовка и работает строительными лесами без второго вызова.
     Порядок ключей в шаблоне — часть контракта, при правках НЕ переставлять.
  3. ДЕТЕРМИНИРОВАННЫЙ ВРАТАРЬ. Схему проверяет код, а не вера в промпт; при нарушении —
     ОДИН ремонтный вызов с текстом нарушения. Средняя цена ~1.15 вызова, худшая — 2.

Осознанно БЕЗ stub-фолбэка (в отличие от ideate): в одно-карточном режиме заглушка —
единственный результат нажатия кнопки, она бы попала в инбокс как настоящая идея.
Провал вратаря = честный отказ (fusion_skip + violations), НЕ пустышка.

Контракт: run(inputs, env) -> dict. Ключ/сеть — только через env["llm"].

inputs:
    picked             : list[dict]  материалы от pick_cross_sample (1+ на источник)
    seed               : int         пишется в карточку (ре-ролл = seed+1)
    min_sources        : int = 2     меньше → честный skip, LLM не дёргаем
    direction          : str|None    руль темы (гнём, но не в ущерб связности)
    rejected           : list|None   забракованное юзером — не приносить похожее
    select_from_pairs  : bool        oversample>1: в каждом источнике пара кандидатов,
                                    LLM выбирает ОДНОГО из пары (второй игнорируется)
    repair_retries     : int = 1     сколько ремонтных вызовов после провала вратаря
returns:
    ideas[0]           : карточка с brain="llm", mode="ultra", seed, combo_hash,
                         sources_used/missing, fusion[{source,role,took,load_bearing,collapse}]
    fusion_skip        : str|None    причина отказа; violations — что именно поймал вратарь
"""

import hashlib
import json
import re

ROLES = ("кто_и_работа", "механизм", "поверхность", "ограничение", "сигнал")

_COLLAGE_MARKERS = (
    " + ",
    "+",
    " и ещё ",
    " а также ",
    " плюс ",
    "2-в-1",
    "два в одном",
    "all-in-one",
    "всё в одном",
)

_PAIRS_HINT = (
    "\nВНИМАНИЕ: материалов БОЛЬШЕ, чем источников — с каждого источника дана ПАРА "
    "кандидатов.\nИз каждой пары выбери ОДНОГО, кто лучше ложится в каркас; второго "
    "игнорируй полностью\n(не упоминай). fusion и source_ids — только про выбранных.\n"
)

FUSE_TMPL = """Ты синтезатор идей. Дано РОВНО по одному материалу с каждого источника:
{items}
{direction}{rejected}{pairs}ЗАДАЧА: одна ультра-идея — один конкретный продукт для ОДНОГО пользователя и ОДНОЙ его работы.

ШАГ 1 — НАЗНАЧЬ РОЛИ (до придумывания идеи). Пять слотов каркаса, название копируешь ДОСЛОВНО:
  кто_и_работа — чей это инструмент и какую работу человек им закрывает
  механизм     — КАК оно работает внутри (приём, алгоритм, схема обмена)
  поверхность  — где человек его касается (канал, интерфейс, момент)
  ограничение  — среда/лимит, который формирует решение (без ключей, оффлайн, 5 сек)
  сигнал       — что служит входными данными/триггером
Каждому материалу — Свой слот, название роли = ТОЛЬКО одно из пяти выше (не изобретай
«контекст», «проблема», «решение» и прочее). Два материала в одном слоте = коллаж = провал.

ШАГ 2 — ПОСТРОЙ ИДЕЮ так, чтобы каждый материал был НЕСУЩИМ: убери его — механика ломается.
Материал вносит МЕХАНИЗМ, а не тему: «про VPN» — тема (запрещено), «туннелирует трафик
через свой же клиент» — механизм (годно).

ЗАПРЕЩЕНО: перечисления «фича из А + фича из Б», «а ещё умеет», склейка двух продуктов,
зонтичные платформы, слово «экосистема».

ТЕСТ НА СВЯЗНОСТЬ (применяешь сам до ответа): мысленно вычеркни любой один материал.
Если идея продолжает работать, просто теряя строчку описания — каркас плохой, переделай.
Должна ломаться механика.

Верни ОДНУ строку JSON, ключи строго в этом порядке:
{{"digest":[{{"source":"...","id":"...","роль":"один из: {roles}","суть":"1 фраза: что там по сути"}}],
"frame":"1 фраза: пользователь + его работа",
"fusion":[{{"source":"...","id":"...","role":"один из: {roles}","took":"какой механизм взяли",
"load_bearing":true,"collapse":"что сломается, если это убрать"}}],
"title":"...","why":"...","effort":"легко|средне|тяжело",
"source_ids":["..."],"verification":"как проверить спрос за день"}}
ВСЕ поля обязательны — ответ без fusion или verification считается негодным.
Никакого текста вне JSON.
"""

REPAIR_TMPL = """Твой прошлый ответ нарушил правила: {violations}
Вот он: {previous}
НЕ переписывай поля вслепую — ПЕРЕСТРОЙ каркас: заново назначь каждому материалу роль
(название дословно из пяти слотов) и построй идею так, чтобы каждый был несущим
(load_bearing=true у ВСЕХ, вклад — механизм, не ярлык). Верни ОДИН JSON целиком.
Не оправдывайся, не добавляй текст вне JSON.
"""


# ---------------------------------------------------------------- helpers


def _format_items(picked):
    lines = []
    for it in picked:
        lines.append(
            "- [%s] id=%s | %s | %s"
            % (
                it.get("source") or "?",
                it.get("id") or "",
                it.get("title") or "",
                it.get("url") or "",
            )
        )
        if it.get("context"):
            lines.append("    контекст: %s" % str(it["context"])[:400])
    return "\n".join(lines)


def build_prompt(picked, direction=None, rejected=None, select_from_pairs=False):
    """Собрать промпт слияния (без вызова LLM) — им же пользуется dry-run обёртки."""
    return FUSE_TMPL.format(
        items=_format_items(picked),
        roles=" | ".join(ROLES),
        direction=("РУЛЬ (гни идею сюда, но не в ущерб связности): %s\n" % direction) if direction else "",
        rejected=("НЕ ПРЕДЛАГАТЬ (владелец уже забраковал): %s\n" % "; ".join(rejected[:20])) if rejected else "",
        pairs=_PAIRS_HINT if select_from_pairs else "",
    )


def _json_candidates(text):
    """Все сбалансированные {...}-объекты из ответа, слева направо.

    Модель иногда рассуждает прозой, выдаёт черновик и финальный JSON, или оставляет
    висячую запятую. Берём КАЖДЫЙ сбалансированный объект как кандидата (с фиксацией
    «,}»/«,]»), валидатор потом выберет первый годный — прозу и черновики отвергнет
    отсутствие обязательных полей.
    """
    if not text:
        return []
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    out, depth, in_str, esc, start = [], 0, False, False, -1
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    frag = t[start : i + 1]
                    for variant in (frag, re.sub(r",\s*([\]}])", r"\1", frag)):  # висячая запятая
                        try:
                            v = json.loads(variant)
                            if isinstance(v, dict):
                                out.append(v)
                                break
                        except ValueError:
                            continue
                    start = -1
    return out


def _validate(card, picked, select_from_pairs=False):
    """Список нарушений каркаса. Пусто = карточка годная. Код, не промпт."""
    v = []
    if not isinstance(card, dict):
        return ["ответ не JSON-объект"]

    for key in ("title", "why", "fusion", "frame"):
        if not card.get(key):
            v.append("нет поля %s" % key)
    if v:
        return v

    sources = {(i.get("source") or "?") for i in picked}
    fusion = card.get("fusion") or []
    if not isinstance(fusion, list):
        return ["fusion должен быть списком"]

    if len({str(f.get("source")) for f in fusion}) < len(sources):
        v.append("в fusion не все источники (нужны: %s)" % ", ".join(sorted(sources)))

    roles = [str(f.get("role") or "").strip() for f in fusion]
    if any(not r for r in roles):
        v.append("у части материалов не указана роль в каркасе")
    # Названия ролей — леса для модели, НЕ планка качества: модель стабильно придумывает
    # свои («транслятор», «детектор») при верной раскладке. Планка — различные слоты
    # (анти-коллаж) и прямой вклад (took/load_bearing/collapse ниже). Канонические
    # названия — что удалось, см. _coerce_schema/_norm_role (для UI/аналитики).
    need_distinct = min(len(fusion), len(ROLES))
    if len(set(roles)) < need_distinct:
        v.append("роли повторяются — это коллаж; каждому материалу свой слот")

    for f in fusion:
        if f.get("load_bearing") is False:
            v.append("материал %s помечен как несущий=false — он приклеен, переделай каркас" % f.get("source"))
        took = str(f.get("took") or "")
        if len(took.split()) < 3:
            v.append("вклад источника %s описан не механизмом, а ярлыком: %r" % (f.get("source"), took))
        if not str(f.get("collapse") or "").strip():
            v.append("нет collapse для %s (что сломается без него)" % f.get("source"))

    title = str(card.get("title") or "")
    low = title.lower()
    for m in _COLLAGE_MARKERS:
        if m in low:
            v.append("в заголовке маркер коллажа %r" % m.strip())
            break
    if low.count(",") >= 2:
        v.append("заголовок — перечисление")

    if not select_from_pairs:
        # режим «ровно 1 на источник»: карточка обязана цитировать ВСЕ выданные материалы.
        # В режиме пар (oversample>1) выбранный — один из пары, полного покрытия не требуется.
        src_ids = {str(i.get("id")) for i in picked}
        got_ids = {str(x) for x in (card.get("source_ids") or [])}
        if not src_ids.issubset(got_ids):
            v.append("source_ids не покрывает все материалы: не хватает %s" % ", ".join(sorted(src_ids - got_ids)))
    return v


# Свободные названия ролей → канонический слот. Живые прогоны 2026-08-15: модель
# пишет «интерфейс_взаимодействия» вместо «поверхность» — идейно верно (слот занят
# осмысленно), номенклатура своя. Коерсия прозрачная: оригинал остаётся в role_raw.
_ROLE_ALIASES = (
    ("механизм", ("механизм", "алгоритм", "приём", "прием", "хранител", "туннел", "обертк", "обёртк", "способ", "как работает")),
    ("кто_и_работа", ("пользовател", "потребител", "разработчик", "кто", "работа", "субъект", "аудитор")),
    ("поверхность", ("поверхност", "интерфейс", "канал", "касани", "точка входа")),
    ("ограничение", ("ограничен", "лимит", "среда", "офлайн", "бюджет")),
    ("сигнал", ("сигнал", "триггер", "вход", "источник")),
)


def _norm_role(name):
    low = str(name or "").lower().strip()
    for canon, keys in _ROLE_ALIASES:
        for k in keys:
            if k in low:
                return canon
    return None


def _coerce_schema(card):
    """Мягкая коерсия схемы ПЕРЕД вратарём (не подделка качества — приведение номенклатуры):
    (1) роли → канонические слоты, оригинал в role_raw; (2) source_ids дополняется id из
    fusion — карточка обязана цитировать то, что реально использовала, и fusion это знает."""
    fusion = card.get("fusion")
    if isinstance(fusion, list):
        for f in fusion:
            if isinstance(f, dict):
                role = str(f.get("role") or "").strip()
                if role and role not in ROLES:
                    canon = _norm_role(role)
                    if canon:
                        f["role_raw"] = role
                        f["role"] = canon
        ids = [str(f.get("id")) for f in fusion if isinstance(f, dict) and f.get("id") not in (None, "")]
        if ids:
            merged = [str(x) for x in (card.get("source_ids") or [])]
            card["source_ids"] = list(dict.fromkeys(merged + ids))
    digest = card.get("digest")
    if isinstance(digest, list):
        for d in digest:
            if isinstance(d, dict):
                role = str(d.get("роль") or "").strip()
                if role and role not in ROLES:
                    canon = _norm_role(role)
                    if canon:
                        d["роль_raw"] = role
                        d["роль"] = canon


def _combo_hash(picked):
    """Отпечаток ВЫБОРКИ (source:id, порядок не важен) — им обёртка ловит повторы."""
    raw = "|".join(sorted("%s:%s" % (i.get("source"), i.get("id")) for i in picked))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- organ


def run(inputs, env):
    inputs = inputs or {}
    env = env or {}
    picked = list(inputs.get("picked") or inputs.get("items") or [])
    select_pairs = bool(inputs.get("select_from_pairs"))
    if len({(i.get("source") or "?") for i in picked}) < int(inputs.get("min_sources", 2)):
        return {"ideas": [], "fusion_skip": "нужно >=2 источников", "attempts": 0, "violations": []}

    ask = env.get("llm")
    if not callable(ask):
        return {"ideas": [], "fusion_skip": "нет живого мозга (env['llm'])", "attempts": 0, "violations": []}

    prompt = build_prompt(
        picked,
        direction=inputs.get("direction"),
        rejected=inputs.get("rejected") or [],
        select_from_pairs=select_pairs,
    )

    attempts, last_raw, violations = 0, "", []
    for attempt in range(1 + max(0, int(inputs.get("repair_retries", 1)))):
        attempts += 1
        raw = ask(
            prompt
            if attempt == 0
            else REPAIR_TMPL.format(violations="; ".join(violations), previous=last_raw[:1500])
        )
        last_raw = raw or ""
        violations = []
        candidates = _json_candidates(last_raw)
        for card in candidates:  # первый прошедший вратаря выигрывает
            _coerce_schema(card)
            violations = _validate(card, picked, select_from_pairs=select_pairs)
            if not violations:
                break
        if candidates and not violations:
            card["brain"] = "llm"  # контракт deliver: не-stub карточка доходит до инбокса
            card["mode"] = "ultra"
            card["seed"] = inputs.get("seed")
            card["combo_hash"] = _combo_hash(picked)
            card["sources_used"] = sorted({(i.get("source") or "?") for i in picked})
            card["sources_missing"] = inputs.get("sources_missing") or []
            card["attempts"] = attempts
            return {
                "ideas": [card],
                "fusion": card.get("fusion"),
                "combo_hash": card["combo_hash"],
                "attempts": attempts,
                "fusion_skip": None,
                "violations": [],
            }

    # Осознанно НЕ отдаём stub-карточку: в одно-карточном режиме заглушка хуже пустоты.
    if not violations:
        violations = ["ответ непарсибелен"]  # кандидатов не было вовсе
    return {
        "ideas": [],
        "fusion": None,
        "combo_hash": _combo_hash(picked),
        "attempts": attempts,
        "violations": violations,
        "raw": last_raw,  # полный сырой ответ — калибровка промпта без слепоты
        "raw_tail": last_raw[-400:],
        "fusion_skip": "слияние не прошло вратаря: %s" % "; ".join(violations[:3]),
    }
