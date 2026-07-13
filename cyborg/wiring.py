"""Обвязка ИСПОЛНЯЕМЫХ органов беты. Подключены органы idea_engine (локальны, чисты,
безопасны: без секретов, без записи в прод). Реестр _shared/organs.json (89 карточек) —
это каталог; сюда по одному переносятся реальные исполняемые органы (совет: расти
группами, а не подключать все 47 сразу).
"""
import json
import os
import subprocess
import sys

_IDEA = "M:/projects/kiborg/idea_engine"
if _IDEA not in sys.path:
    sys.path.insert(0, _IDEA)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from organs import collect_source, ideate, finish_step, rank_ideas, readability_gate  # noqa: E402
from core import Organ  # noqa: E402
import deliver  # noqa: E402  (cyborg/deliver.py — sink в инбокс idea_engine)
import finish_sink  # noqa: E402  (sink: доводит nudge «доделай» до инбокса, вычистив секреты)
import stash_sink  # noqa: E402  (sink: копит идеи в копилку без потолка — для автономных прогонов)
import seen_items  # noqa: E402  (фильтр «уже видели» по ID сырых items — только для харвеста)
from organs_vendored import scrub_secrets  # noqa: E402  (вендорен из реестра, чистый)
import mind  # noqa: E402  (движок взвешенного совещания — отбор идей советом, не одним судьёй)
import advisors  # noqa: E402  (три советника: арбитр rank_ideas + интуиция ask_llm + оркестр)

RECON = "M:/projects/panelofprojects/recon.json"


def _run_collect(inputs, env):
    # ВАЖНО: раньше env игнорировался (жёстко n=8/source=hn) — расширение харвеста
    # (SOURCE_N) реально не долетало до сборщика в живом прогоне, только до gate-проверки
    # в _source_signature (та звала collect_source напрямую). Теперь настройки прокидываются.
    env = env if isinstance(env, dict) else {}
    e = {"n": env.get("n", 8), "source": env.get("source", "hn")}
    if env.get("sources"):
        e["sources"] = env["sources"]
    if env.get("timeout"):
        e["timeout"] = env["timeout"]
    # keyed-источники (telegram) читают креды/конфиг из env по своим ключам — их тоже надо
    # ПРОКИНУТЬ, иначе источник в списке sources есть, а данных для него нет → тихо падает
    # в partial_errors («no channels configured»). Тот же класс бага, что был с sources/SOURCE_N:
    # env собирается заново и часть ключей теряется по дороге. Прокидываем все telegram_*.
    for k in ("telegram_channels", "telegram_api_id", "telegram_api_hash", "telegram_session",
              "telegram_python", "telegram_timeout"):
        if env.get(k) is not None:
            e[k] = env[k]
    # Глаза ТОЛЬКО смотрят — приносят всё, что увидели, без фильтра «уже видели». Помнить,
    # что уже обдумывали, — работа Мозга (см. _run_ideate): фильтр переехал туда 2026-07-13,
    # чтобы метафора не врала (смотреть ≠ помнить).
    return collect_source.run(inputs, e)


def _content_llm(env):
    """Живая модель для контентных органов (ideate/rank): env['content_llm'], иначе общий env['llm'].
    Так генератор и судья идут на живой модели, даже когда мозг оставлен на детерминированном stub."""
    env = env if isinstance(env, dict) else {}
    llm = env.get("content_llm") or env.get("llm")
    return llm if callable(llm) else None


def _run_ideate(inputs, env):
    inp = inputs or {}
    # ПАМЯТЬ — работа Мозга, не Глаз (2026-07-13, переехало из _run_collect). Фильтр «уже
    # видели» — ТОЛЬКО когда явно попросили (харвест ставит флаг в env). Интерактивный
    # «приноси идеи» (панель, ручной клик) флаг не ставит — юзер жмёт кнопку, ожидая идей
    # СЕЙЧАС, а не «а тут всё уже старое, пропускаю». filter_fresh отмечает виденным ровно
    # то, что реально уходит на генерацию — не раньше.
    if env.get("filter_seen_items") and inp.get("items"):
        inp = dict(inp)
        inp["items"] = seen_items.filter_fresh(inp["items"])
    e = {"k": 12}  # режим «максимум качества»: генерим 12 кандидатов — судье есть из чего отобрать лучшее
    llm = _content_llm(env)
    if llm:
        e["llm"] = llm
    return ideate.run(inp, e)


class _IntuitionNoCap(advisors.AskLlmAdvisor):
    """Интуиция (ask_llm) БЕЗ потолка на ответ (реш. юзера 2026-07-13: «убрать ограничение
    вообще»). Рассуждающие модели closerouter при max_tokens=256 тратят весь лимит на скрытое
    рассуждение и возвращают пусто → интуиция молчит. Проверено: без max_tokens deepseek
    досказывает рассуждение (~1000 токенов) и отдаёт баллы.

    Копия родительского _ask с одним отличием — в payload НЕТ ключа max_tokens (модель берёт
    свой дефолт-бюджет). Это единственный обход чужого хардкода: их advisors.py не трогаем.
    Когда параллельная сессия добавит max_tokens в context — этот подкласс удалить."""

    def _ask(self, chain, prompt, budget_ms):
        if not os.path.exists(self._js):
            return None
        n = max(1, len(chain))
        per_provider_ms = max(3000, budget_ms // n)
        payload = {"inputs": {"prompt": prompt, "temperature": 0.2},   # без max_tokens — потолок снят
                   "env": {"chain": chain, "timeout_ms": per_provider_ms}}
        try:
            proc = subprocess.run([self._node, self._js], input=json.dumps(payload),
                                  capture_output=True, text=True, encoding="utf-8",
                                  timeout=max(5, budget_ms // 1000 + 5))
        except Exception:
            return None
        if proc.returncode != 0 and not proc.stdout.strip():
            return None
        try:
            res = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            return None
        return res.get("text") if res.get("ok") else None


def _council_no_cap(context=None):
    """Тот же совет (advisors.build_council), но голос интуиции — БЕЗ потолка (_IntuitionNoCap).
    Арбитр и оркестр берём как есть из их модуля; подменяем только ask_llm."""
    return [_IntuitionNoCap() if getattr(a, "name", "") == "ask_llm" else a
            for a in advisors.build_council(context)]


def _rank_by_council(inputs, env, keep):
    """Отбор топ-keep идей ВЗВЕШЕННЫМ СОВЕТОМ (mind.deliberate), а не одиночным судьёй.

    Совет = арбитр rank_ideas (0.41) + интуиция ask_llm (0.39) + оркестр (0.20). Оркестр
    голосует ВСЕГДА, когда есть ключи (реш. юзера: совет зовётся всегда, а не по сомнению
    интуиции — «умный сомневается всегда»). Потому deliberate (плоский, все голосуют
    безусловно), а НЕ think (там оркестр за эскалацией). Совет ставит балл каждой идее,
    берём топ-keep по итоговому баллу — так форма ideas_best (список) цела,
    downstream (scrub/deliver) не трогаем.

    Возвращает {'ideas_best':[...]} когда проголосовал хоть один советник (арбитр внутри
    mind.think опрашивается первым, живой моделью — его результат и переиспользуем, чтобы НЕ
    звать rank_ideas.run повторно). solo=True в метаданных = по факту судил один арбитр.
    None только если воздержались ВСЕ (degraded) -> вызыватель идёт на плоский rank_ideas."""
    ideas = list((inputs or {}).get("ideas") or [])
    if len(ideas) <= keep:
        return {"ideas_best": ideas}                # отбирать не из чего — отдаём как есть
    # варианты для совета: копия идей с явным id=индекс, чтобы вернуть ИСХОДНЫЕ дикты по id
    options, orig = [], {}
    for i, d in enumerate(ideas):
        base = dict(d) if isinstance(d, dict) else {"title": str(d)}
        options.append({**base, "id": i})
        orig[i] = d
    # Оркестр теперь голосует на КАЖДОМ отборе (горячий путь) и судит весь пул идей подряд.
    # Чтобы 6 идей × рецензент не вылезли за таймаут пульта (180с): гоним ВСЕХ рецензентов
    # параллельно (max_workers = число моделей) и держим короткий бюджет на идею. Настройки
    # кладём в cfg здесь — keychain/advisors их принимают, но сами не трогаются.
    orch = env.get("orchestra")
    if isinstance(orch, dict) and orch.get("models"):
        orch = {**orch, "max_workers": len(orch["models"]),
                "timeout_sec": int(env.get("orchestra_timeout_sec", 45))}
    context = {
        "content_llm": _content_llm(env),           # оживляет арбитра живой моделью (иначе фолбэк-порядок)
        "llm_chain": env.get("llm_chain"),          # оживляет интуицию (цепочка провайдеров с ключами)
        "orchestra": orch,                          # оркестр: голосует всегда (параллельно, короткий бюджет)
        "llm_timeout_ms": env.get("llm_timeout_ms", 45000),
    }
    # deliberate = плоский совет: арбитр + интуиция + оркестр голосуют ВСЕ и ВСЕГДА (кто без
    # ключа — сам воздержится). Не think: там оркестр спал, пока интуиция не засомневается —
    # ровно та «пропущу совет, раз уверен» логика, которую юзер не хотел.
    verdict = mind.deliberate(
        "Отбери лучшие идеи для доставки: оригинальность, польза, выполнимость.",
        options, _council_no_cap(context), context)
    live = verdict.get("live") or []
    if verdict.get("degraded") or not live:          # никто не проголосовал -> плоский откат на судью
        return None
    # Арбитр внутри mind.think УЖЕ отработал живой моделью (его опрашивают первым). Поэтому и
    # когда голос один (интуиция/оркестр промолчали), берём готовый результат ОТСЮДА, а не зовём
    # rank_ideas.run повторно — иначе второй платный вызов той же модели (нашёл скептик 2026-07-13).
    scores = verdict.get("scores") or {}
    ranked = sorted(orig, key=lambda oid: (-float(scores.get(oid, 0.0)), oid))  # по баллу, стабильно
    solo = len(live) < 2                             # по факту судил один арбитр (честная пометка)
    tag = "solo" if solo else "council"
    best = [dict(orig[oid], judged=tag) if isinstance(orig[oid], dict) else orig[oid]
            for oid in ranked[:keep]]
    return {"ideas_best": best,
            "council": {"live": live, "solo": solo, "woken": ("orchestra" in live),
                        "why": verdict.get("why")}}


def _run_readability(inputs, env):
    """Редактор читаемости: карточкам-победителям (ideas_best) ставит балл читаемости и
    описание ниже порога переписывает самонесущим. Идею НЕ теряем, карточку НЕ выкидываем —
    правим только текст why. Живёт ПОСЛЕ отбора (чиним лишь то, что реально уйдёт в кучу) и
    ДО scrub (переписанный текст тоже проходит вычистку секретов). Без ключа — passthrough."""
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
    return readability_gate.run(inputs, e)


def _run_rank(inputs, env):
    env = env if isinstance(env, dict) else {}
    e = {"keep": 5}  # режим «максимум качества»: оставить топ-5 из 12 (жёсткий отбор ~40%, куча без потолка)
    llm = _content_llm(env)
    if llm:
        e["llm"] = llm
    # СОВЕТ в живом цикле (гейт снят юзером 2026-07-13, ход Г): идеи судит взвешенный совет,
    # если есть 2-й живой голос (в env принесли цепочку интуиции / оркестр). Иначе — прежний
    # одиночный судья, офлайн байт-в-байт. Любой сбой совета -> тихий откат, конвейер не встаёт.
    if env.get("council") is not False and (env.get("llm_chain") or env.get("orchestra")):
        try:
            out = _rank_by_council(inputs, env, keep=int(e["keep"]))
            if out is not None:
                return out
        except Exception:
            pass                                     # совет никогда не роняет отбор идей
    return rank_ideas.run(inputs, e)


_CURSOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "finish_cursor.json")


def _run_finish(inputs, env):
    # ПАМЯТЬ (2026-07-13): курсор — тоже работа Мозга, не Ног. Ноги (finish_step) просто идут
    # туда, куда сказали; ПОМНИТЬ, на каком проекте остановились, — не их дело. Настоящего
    # Мозг-органа в цепочке «доделать» нет (finish_step сам источник), поэтому решение живёт
    # тут, в нервах — на пульте помечено честным узлом «🧠 Мозг (в нервах)» перед Ногами.
    # Курсор ПЕРСИСТИТСЯ между прогонами — иначе finish_step всегда возвращал первый проект
    # (память per-run, «cursor» в ней не появлялся; finish_step отдаёт «next_cursor»). Теперь
    # «доделай» реально ротирует по проектам бэклога.
    cursor = 0
    try:
        with open(_CURSOR_FILE, encoding="utf-8") as f:
            cursor = int(json.load(f).get("cursor", 0))
    except Exception:
        pass
    out = finish_step.run(inputs, {"recon_path": RECON, "cursor": cursor})
    try:
        os.makedirs(os.path.dirname(_CURSOR_FILE), exist_ok=True)
        with open(_CURSOR_FILE, "w", encoding="utf-8") as f:
            json.dump({"cursor": int(out.get("next_cursor", cursor + 1))}, f)
    except Exception:
        pass
    return out


def _run_deliver(inputs, env):
    return deliver.run(inputs, env)


def _liver_clean(rec):
    """Печень (scrub_secrets): прогоняет текстовые поля записи через вычистку секретов.
    Чистка — работа Печени, не руки. Возвращает копию с вычищенными title/why."""
    clean = dict(rec)
    for f in ("title", "why"):
        if isinstance(clean.get(f), str):
            clean[f] = scrub_secrets.scrub_text(clean[f])
    return clean


def _run_finish_sink(inputs, env):
    # Нервы ведут нудж СНАЧАЛА через Печень (scrub_secrets), ПОТОМ в руку (finish_sink).
    # Рука больше не чистит сама (раньше _scrub_nudge был внутри finish_sink — рука делала
    # работу Печени). Метафора честная: Печень фильтрует, Рука кладёт, нервы соединяют.
    inp = inputs or {}
    nudge = inp.get("nudge")
    if isinstance(nudge, dict) and nudge:
        inp = {**inp, "nudge": _liver_clean(nudge)}   # Печень чистит нудж
    return finish_sink.run(inp, env)                   # Рука кладёт уже вычищенное


def _run_stash(inputs, env):
    return stash_sink.run(inputs, env)


def _run_scrub(inputs, env):
    inp = inputs or {}
    ideas = list(inp.get("ideas_polished") or inp.get("ideas_best") or inp.get("ideas") or [])
    out, red = [], 0
    for idea in ideas:
        if isinstance(idea, dict):
            clean = dict(idea)
            for f in ("title", "why"):
                if isinstance(clean.get(f), str):
                    s = scrub_secrets.scrub_text(clean[f])
                    if s != clean[f]:
                        red += 1
                    clean[f] = s
            out.append(clean)
        else:
            out.append(idea)
    return {"ideas_safe": out, "redacted": red}


def build_organs():
    return [
        Organ(
            name="collect_source",
            purpose="Тянет свежие внешние items (новости/сигналы) — сырьё для идей.",
            run=_run_collect, role="source", produces=["items"], consumes=[],
            tags=["собрать", "новости", "свежие", "источник", "идеи", "сигналы", "сырьё"],
            needs={"network": True},
        ),
        Organ(
            name="ideate",
            purpose="Из items делает МНОГО идей-кандидатов с ценником (судья отберёт лучшие).",
            run=_run_ideate, role="transform", produces=["ideas"], consumes=["items"],
            tags=["идея", "идеи", "идей", "придумать", "предложить"],
            needs={"key": "LLM_KEY", "stub_ok": True},
        ),
        Organ(
            name="rank_ideas",
            purpose="Судья: из пула идей оставляет топ-3 по рубрике (оригинальность/польза/выполнимость).",
            run=_run_rank, role="transform", produces=["ideas_best"], consumes=["ideas"],
            tags=["идея", "идеи", "отобрать", "лучшие", "оценить", "судья", "ранжировать"],
            needs={"key": "LLM_KEY", "stub_ok": True},
        ),
        Organ(
            name="finish_step",
            purpose="Режим 'доделать': достаёт следующий шаг по существующим проектам.",
            run=_run_finish, role="source", produces=["nudge"], consumes=[],
            tags=["доделать", "существующие", "проекты", "шаг", "финиш", "довести"],
            needs={},
        ),
        Organ(
            name="readability_gate",
            purpose="Редактор читаемости: карточку с мутным описанием (балл<7) переписывает самонесущей, идею не теряя.",
            run=_run_readability, role="transform", produces=["ideas_polished"], consumes=["ideas_best"],
            tags=["читаемость", "понятно", "описание", "идеи", "редактор", "ясно"],
            needs={"key": "LLM_KEY", "stub_ok": True},
        ),
        Organ(
            name="scrub_secrets",
            purpose="Защитный проход: вычищает креды (sk-/ghp-/AIza/KEY=…) из текста идей перед доставкой.",
            run=_run_scrub, role="transform", produces=["ideas_safe"], consumes=["ideas_polished"],
            tags=["безопасно", "секрет", "очистить", "идеи", "защита"],
            needs={},
        ),
        Organ(
            name="deliver",
            purpose="Доставляет идеи в инбокс через очередь (потолок 3 + обратная тяга, inbox.md).",
            run=_run_deliver, role="sink", produces=["delivered"], consumes=["ideas_safe"],
            tags=["доставить", "идеи", "инбокс", "прислать", "приноси", "свежие"],
            needs={},
        ),
        Organ(
            name="finish_sink",
            purpose="Доводит подсказку «доделай» до инбокса (через deliver), вычистив секреты из recon.",
            run=_run_finish_sink, role="sink", produces=["delivered"], consumes=["nudge"],
            tags=["доделать", "довести", "шаг", "инбокс", "проекты"],
            needs={},
        ),
    ]


def build_harvest_organs():
    """Органы АВТОНОМНОГО режима «копилка»: тот же конвейер идей (collect -> ideate ->
    rank -> scrub), но финальный sink — НЕ инбокс (потолок 3), а копилка без потолка.
    Для прогонов, когда Claude гоняет киборга сам: идеи копятся горой, разберёт юзер.
    """
    chain = [o for o in build_organs()
             if o.name in ("collect_source", "ideate", "rank_ideas", "readability_gate", "scrub_secrets")]
    chain.append(Organ(
        name="stash_ideas",
        purpose="Копит идеи в копилку без потолка (для автономных прогонов), с дедупом.",
        run=_run_stash, role="sink", produces=["delivered"], consumes=["ideas_safe"],
        tags=["копилка", "копить", "накопитель", "идеи", "автономно", "приноси", "свежие"],
        needs={},
    ))
    return chain
