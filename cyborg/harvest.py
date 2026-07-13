"""Автономный сбор идей в КОПИЛКУ (для моментов, когда Claude гоняет киборга сам).

Тот же конвейер, что «приноси идеи» (collect -> ideate -> rank -> scrub), но финальный
sink — копилка БЕЗ потолка (stash), а не инбокс с потолком 3. Идеи копятся горой, дедуп
отсеивает повторы; разберёт человек, когда вернётся.

Запуск:
    python harvest.py         — один прогон
    python harvest.py 5       — 5 прогонов подряд (за один вызов набрать больше)

Результат: cyborg/data/idea_stash.md (человеку) + idea_stash.jsonl (машине).
Каждый прогон логируется в data/runs.md (как и обычные прогоны).
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import hashlib  # noqa: E402
import json  # noqa: E402

from wiring import build_harvest_organs  # noqa: E402
from orchestrator import Cyborg  # noqa: E402
import ask_llm  # noqa: E402
import stash  # noqa: E402
import seen_items  # noqa: E402
from organs_vendored import scrub_secrets  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATE_FILE = os.path.join(DATA, "harvest_state.json")
STATUS_FILE = os.path.join(DATA, "source_status.json")  # живой per-source статус для пульта

# Сколько заголовков тянуть за прогон СУММАРНО (бюджет делится между источниками в
# collect_source). Дефолт органа collect_source — 8; [D] беру 30: КОРЕНЬ «копилка застряла
# на 6» — узкий источник (топ-8 HN меняется раз в часы → те же идеи → дедуп режет). Шире
# слой = разнообразнее сырьё для ideate + гейт видит churn глубже (меньше холостых
# пропусков). Это КОНФИГ (мой файл), а не правка ядра: collect_source читает n/sources из
# env по дизайну. Тюнить — здесь.
SOURCE_N = 30

# Источники, что мержим за один прогон (2026-07-12: было только HN, потом +reddit/lobsters/
# gh_trending). Product Hunt отложен — нужен токен (гейт юзера).
# "telegram" (2026-07-12) — единственный КЛЮЧЕВОЙ источник: читает каналы через личный ТГ-аккаунт
# (орган collect_tg_news, вендорен из darbot). Креды/сессия резолвятся в _harvest_env ниже —
# без них telegram сам себя выключает (ValueError "no channels", errors, не крашит прогон).
# Урезано до 1 источника (2026-07-13) по просьбе юзера — «оставь 1 любой паблик для идей».
# Держим только telegram (проверенный, на нём строим «закладку дочитывания»). Остальные 4
# сохранены закомментированными — вернуть охват = раскомментировать нижнюю строку.
SOURCES = ["telegram"]
# SOURCES = ["hn", "reddit", "lobsters", "gh_trending", "telegram"]  # полный набор

# Каналы под тематику kiborg (тех/AI/pet-проекты) — НЕ список darbot (тот про новости/политику/
# экономику, другая тема). @tproger — мой стартовый кандидат, подтверждён живым смоуком 2026-07-12.
# Остальные 20 — из папки юзера (t.me/addlist/gUpAozY8_SI0ZTVi, тема "AI 🤖"), разрешена read-only
# (chatlists.CheckChatlistInvite — НЕ подписка, только просмотр состава) 2026-07-12, все настоящие.
# Урезано до 1 канала (2026-07-12) по просьбе юзера — чтобы НАГЛЯДНО наблюдать работу органа
# источников на одном пабликe (зашёл → прочитал пост → подумал). 2026-07-13: добавлен второй,
# выбран случайно (random.choice) из оставшихся 19 — @ai_machinelearning_big_data. Остальные 18
# сохранены ниже закомментированными: вернуть полный охват — просто раскомментировать (список
# подтверждён живым в этот же день). @tproger — стартовый канал, подтверждён живым смоуком.
TELEGRAM_CHANNELS = [
    "@tproger",
    "@ai_machinelearning_big_data",  # выбран случайно 2026-07-13, подтверждён смоуком ниже
    # --- временно отключены (наблюдательный режим на 2 каналах) ---
    # "@unitool", "@llm_under_hood", "@gpt_news", "@hiaimedia", "@openai_fan",
    # "@data_secrets", "@machinelearning_interview", "@data_analysis_ml", "@neuro_code",
    # "@neuraldvig", "@aitshnya", "@seeallochnaya", "@gptpublic", "@ai_newz",
    # "@notboring_tech", "@lovedeathtransformers", "@machinelearning_ru", "@boris_again",
    # "@techsparks",
]

# Какие источники ЛИЧНО проверены юзером (не «бета»). Пока — только telegram: каналы юзер
# сам курировал из своей папки, @tproger подтвердил живым смоуком. Остальные 4 (hn/reddit/
# lobsters/gh_trending) подключены, крутятся, но юзером персонально НЕ провалидированы —
# пульт метит их «β» (бета). Это метаданные доверия, не живой статус: живой 🟢/🔴 считает
# _status_from_out по фактическому улову, а beta — статичный признак отсюда. Расширять по мере
# того, как юзер подтверждает источник вручную.
USER_VERIFIED_SOURCES = {"telegram"}

_DARBOT_ENV = "M:/projects/darbot/.env"
_KIBORG_TG_SESSION = os.path.join(DATA, "kiborg_tg.session")


def _load_darbot_tg_creds():
    """Читает TG_API_ID/TG_API_HASH из .env darbot (read-only, чужой файл не трогаем) — те же
    app-креды, что у бота, приложение Telegram одно и то же. Сессия — СВОЯ копия kiborg
    (cyborg/data/kiborg_tg.session), не тот файл, что живой darbot-бот держит открытым.
    Нет .env/ключей -> (None, None), _telegram сам мягко деградирует (errors, не крашит)."""
    if not os.path.exists(_DARBOT_ENV):
        return None, None
    vals = {}
    with open(_DARBOT_ENV, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals.get("TG_API_ID"), vals.get("TG_API_HASH")


def _harvest_env():
    """env харвест-прогона: живой мозг (если есть ключ) + ШИРОКИЙ смешанный источник
    + фильтр «уже видели» (по ID items, не по тексту идей — см. seen_items.py). Флаг
    ТОЛЬКО здесь: интерактивный «приноси идеи» (панель, ручной клик) его не ставит."""
    env = {"n": SOURCE_N, "sources": SOURCES, "filter_seen_items": True}
    api_id, api_hash = _load_darbot_tg_creds()
    if api_id and api_hash and os.path.exists(_KIBORG_TG_SESSION):
        env["telegram_channels"] = TELEGRAM_CHANNELS
        env["telegram_api_id"] = api_id
        env["telegram_api_hash"] = api_hash
        env["telegram_session"] = _KIBORG_TG_SESSION
    if ask_llm.available():
        env["content_llm"] = ask_llm.ask
    return env


def _titles_sig(titles):
    """Отпечаток набора заголовков (порядок не важен, изменение — важно)."""
    return hashlib.sha1("|".join(sorted(titles)).encode("utf-8")).hexdigest()


def _status_from_out(out):
    """Живой per-source статус из выхлопа collect_source (для пульта): сколько items дал
    каждый источник и упал ли он. ok = дал >=1 item и не в partial_errors. Все упали / нет
    сети -> degraded=True, у всех ok=False. Чистая функция (без I/O) — персист делает main()."""
    items = out.get("items") or []
    counts = {}
    for it in items:
        src = it.get("source") if isinstance(it, dict) else None
        if src:
            counts[src] = counts.get(src, 0) + 1
    errs = {}
    for e in (out.get("partial_errors") or []):
        errs[str(e).split(":", 1)[0].strip()] = str(e)
    sources = {}
    for name in SOURCES:
        cnt = counts.get(name, 0)
        sources[name] = {"items": cnt, "ok": cnt > 0 and name not in errs,
                         "error": errs.get(name),
                         "beta": name not in USER_VERIFIED_SOURCES}  # β в пульте: юзером не проверен
    return {"checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "degraded": bool(out.get("degraded")), "sources": sources}


def _persist_status(status):
    """Атомарно пишет живой статус источников (переиспользуем атомарную запись копилки)."""
    stash.Stash._atomic_write(STATUS_FILE, json.dumps(status, ensure_ascii=False))


def _source_signature():
    """ДЁШЕВО (без LLM) снять отпечаток источника: тот же HTTP, что делает collect_source,
    но БЕЗ дорогих ideate/rank. Чтобы не гонять Gemini впустую, когда ВСЕ ленты не изменились
    ИЛИ изменились, но всё новое мы уже разбирали раньше (fresh_n==0 — точнее, чем просто хеш).
    Отпечаток покрывает ОБЪЕДИНЕНИЕ источников (SOURCES) — иначе смена в reddit/lobsters/
    gh_trending при неизменном HN давала бы ложный gate-пропуск.
    Возвращает (sig|None, degraded, fresh_n|None, status|None). None-хвосты -> не смогли снять.
    status — живой per-source расклад (тот же fetch, что и отпечаток — БЕЗ доп. сети).
    NB: count_fresh — non-mutating, ничего не отмечает виденным (отметка — только в реальном
    прогоне, внутри wiring._run_collect, чтобы не терять сырьё на прогонах, что сами же пропустили)."""
    try:
        from organs import collect_source  # idea_engine/organs (путь добавлен wiring)
        # ПОЛНЫЙ env прогона (вкл. telegram-креды), а не голые n/sources — чтобы отпечаток И статус
        # видели ВСЕ 5 источников так же, как реальный прогон. Иначе telegram без кредов в пробе
        # ложно «упал» (no channels), а он в прогоне работает. Цена — один pyrogram-спавн на
        # гейт-проверку (раз в ~30 мин); зато gate ловит и telegram-churn, а статус честен.
        out = collect_source.run({}, _harvest_env())
    except Exception:
        return None, False, None, None
    items = out.get("items") or []
    titles = [(it.get("title", "") if isinstance(it, dict) else str(it)) for it in items]
    return (_titles_sig(titles), bool(out.get("degraded")),
            seen_items.count_fresh(items), _status_from_out(out))


def _last_sig():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f).get("sig")
    except Exception:
        return None


def _should_run(sig, force, fresh_n=None):
    """Гонять ли прогон: force (ручной клик — намерение юзера перебивает гейт) ИЛИ отпечаток
    не снялся (sig=None) ИЛИ лента изменилась — И (когда посчитан) реально есть fresh_n>0
    items, что мы ещё не видели (иначе лента «изменилась» просто перетасовкой старья —
    точный пропуск, не гонять ideate впустую). fresh_n не передан (старые вызовы/тесты) —
    прежнее поведение по одному хешу."""
    if force or sig is None:
        return True
    if fresh_n is not None and fresh_n == 0:
        return False
    return sig != _last_sig()


def _save_sig(sig):
    stash.Stash._atomic_write(STATE_FILE, json.dumps({"sig": sig}, ensure_ascii=False))


def _log(goal, out):
    os.makedirs(DATA, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    steps = " -> ".join(t.get("organ") for t in out["trace"] if t.get("organ")) or "—"
    r = out.get("result")
    rv = (str(r)[:120] if r is not None else "нет")
    line = f"- [{ts}] «{goal}» → {steps} | {out['deliverable']}={rv}\n"
    with open(os.path.join(DATA, "runs.md"), "a", encoding="utf-8") as f:
        f.write(scrub_secrets.scrub_text(line))


def main(argv):
    force = "--force" in argv or "force" in argv           # ручной клик из пульта перебивает гейт
    nums = [a for a in argv if a.isdigit()]
    n = int(nums[0]) if nums else 1
    n = max(1, min(n, 50))  # предохранитель: не больше 50 прогонов за вызов
    goal = "приноси свежие идеи в копилку"
    env = _harvest_env()
    mode = (f"идеи={ask_llm._MODEL}" if ask_llm.available() else "идеи=stub (ключа нет)") \
        + f" · источники={'+'.join(SOURCES)} (бюджет {SOURCE_N})" + (" · force" if force else "")

    cy = Cyborg(build_harvest_organs(), safe_mode=True)
    total, skipped = 0, 0
    for i in range(n):
        # гейт «есть что нового?» — не гоняем Gemini впустую (а) на неизменной ленте ИЛИ
        # (б) на ленте, что перетасовалась, но всё «новое» мы уже разбирали раньше (fresh_n).
        # force (ручной клик) гейт перепрыгивает: юзер просит собрать СЕЙЧАС — и тогда отпечаток
        # даже не снимаем (иначе лишний fetch 31 заголовка ради результата, который всё равно игнорим).
        if force:
            sig, fresh_n = None, None
        else:
            sig, _degraded, fresh_n, status = _source_signature()
            if status:
                _persist_status(status)   # живой статус источников для пульта (даже если прогон пропустим)
        if not _should_run(sig, force, fresh_n):
            skipped += 1
            why = "нет новых items (уже разбирали)" if fresh_n == 0 else "источник не изменился"
            print(f"прогон {i + 1}/{n}: {why} — пропуск (без вызова LLM)")
            continue
        out = cy.run(goal, env=env)
        r = out.get("result")
        added = r if isinstance(r, int) else 0
        total += added
        if sig is not None:
            _save_sig(sig)   # запоминаем ленту только после реального прогона
        _log(goal, out)
        print(f"прогон {i + 1}/{n}: +{added} свежих идей в копилку")

    st = stash.Stash()
    print(f"\n{mode}")
    print(f"ЗА ВЫЗОВ добавлено: {total} | пропущено (лента не менялась): {skipped} | "
          f"ВСЕГО в копилке: {len(st.ideas)}")
    print(f"копилка (человеку): {st.md}")


if __name__ == "__main__":
    main(sys.argv[1:])
