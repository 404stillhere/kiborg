"""Автономный сбор идей — «фон» киборга (когда он гоняет сам по таймеру с пульта).

ТОТ ЖЕ конвейер и ТА ЖЕ куча, что у ручной кнопки «Принеси идеи» (collect -> ideate ->
rank -> scrub -> deliver в инбокс). Разница только в поведении фона: гейт «есть что нового?»
(пустые прогоны пропускаем) + фильтр «уже видели» (не тащим одни и те же посты). Идеи
копятся в одну кучу без потолка, дедуп отсеивает повторы; разбираешь в своём темпе.

Запуск:
    python harvest.py         — один прогон
    python harvest.py 5       — 5 прогонов подряд (за один вызов набрать больше)

Каждый прогон логируется в data/runs.md (как и ручные прогоны).
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

from wiring import build_organs, _collect_locked  # noqa: E402  (цепочка + фетч под замком tg-сессии)
from orchestrator import Cyborg  # noqa: E402
import ask_llm  # noqa: E402
import keychain  # noqa: E402  (ключи -> совет на отборе; впаивается wire_council для ОБЕИХ кнопок)
import direction  # noqa: E402  (руль темы: env["direction"] для генератора/судьи)
import folders  # noqa: E402  (папки-источник: env["files_paths"], список правится в пульте)
import feeds  # noqa: E402  (ленты-источник: какие публичные ленты включены, тумблеры в пульте)
import seen_items  # noqa: E402
from organs_vendored import scrub_secrets  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# автосбор доставляет в ИНБОКС idea_engine (через deliver), НЕ в копилку — отчёт строим по инбоксу
_IE_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "idea_engine", "data")
STATE_FILE = os.path.join(DATA, "harvest_state.json")
STATUS_FILE = os.path.join(DATA, "source_status.json")  # живой per-source статус для пульта

# Сколько заголовков тянуть за прогон СУММАРНО (бюджет делится между источниками в
# collect_source). Дефолт органа collect_source — 8; [D] беру 30: КОРЕНЬ «копилка застряла
# на 6» — узкий источник (топ-8 HN меняется раз в часы → те же идеи → дедуп режет). Шире
# слой = разнообразнее сырьё для ideate + гейт видит churn глубже (меньше холостых
# пропусков). Это КОНФИГ (мой файл), а не правка ядра: collect_source читает n/sources из
# env по дизайну. Тюнить — здесь.
# Поднят 30→105 (режим «максимум качества», деньги/время не важны): 105 // 21 канал = 5 свежих
# постов с каждого → ~105 заголовков-семян вместо 21. Больше и разнообразнее сырья для ideate.
SOURCE_N = 105

# Источники, что мержим за один прогон. Product Hunt отложен — нужен токен (гейт юзера).
# "telegram" — КЛЮЧЕВОЙ источник: читает каналы через личный ТГ-аккаунт (орган collect_tg_news,
# вендорен из darbot). Креды/сессия резолвятся в _harvest_env ниже — без них telegram сам себя
# выключает (ValueError "no channels", errors, не крашит прогон).
#
# Какие ленты ВКЛЮЧЕНЫ — теперь решает юзер тумблерами в пульте (cyborg/feeds.py, data/feeds.json),
# а не константа тут (2026-07-14). Дефолт (нет файла) = feeds.DEFAULT_FEEDS = ["telegram"] — то же
# поведение, что было захардкожено. Историю урезания охвата 5→1 (2026-07-13) заменил живой тумблер.

# Папки-источник (2026-07-14): киборг читает текстовые файлы из заданных папок как ещё одно
# СЫРЬЁ и смотрит на них НЕЙТРАЛЬНО — как на чужой проект со стороны (не «свой код», без «чини
# себя»: так идеи честнее). Список папок живёт в data/folders.json и правится В ПУЛЬТЕ мышкой
# (см. cyborg/folders.py) — пусто = источник «files» выключен. Секреты (*.env/*.session/ключи)
# и мусор (.git/venv/node_modules/__pycache__) орган пропускает сам (collect_source._files).


def _active_sources():
    """Источники прогона: включённые в пульте ленты (feeds) + 'files', ЕСЛИ заданы папки
    (иначе files молчал бы холостой ошибкой 'no folders'). Единая точка правды для env /
    статуса пульта / лога. Все ленты выключены и папок нет -> [] (пульт предупреждает)."""
    return feeds.enabled() + (["files"] if folders.current() else [])

# Каналы под тематику kiborg (тех/AI/pet-проекты) — НЕ список darbot (тот про новости/политику/
# экономику, другая тема). @tproger — мой стартовый кандидат, подтверждён живым смоуком 2026-07-12.
# 21 канал: @tproger (стартовый, подтверждён живым смоуком) + 20 из папки юзера
# (t.me/addlist/gUpAozY8_SI0ZTVi, тема "AI 🤖"), разрешена read-only (chatlists.CheckChatlistInvite
# — НЕ подписка, только просмотр состава) 2026-07-12, все настоящие, список подтверждён живым 2026-07-13.
# История охвата: 2026-07-12 урезан до 1 канала для наглядного наблюдения органа источников →
# 2026-07-13 второй → 2026-07-13 ВОЗВРАЩЁН полный охват (20 AI + tproger) по просьбе юзера.
# Список длиннее бюджета n — _telegram() берёт случайную выборку каждый прогон (ротация по времени).
TELEGRAM_CHANNELS = [
    "@tproger",
    "@ai_machinelearning_big_data",
    "@unitool", "@llm_under_hood", "@gpt_news", "@hiaimedia", "@openai_fan",
    "@data_secrets", "@machinelearning_interview", "@data_analysis_ml", "@neuro_code",
    "@neuraldvig", "@aitshnya", "@seeallochnaya", "@gptpublic", "@ai_newz",
    "@notboring_tech", "@lovedeathtransformers", "@machinelearning_ru", "@boris_again",
    "@techsparks",
]

# Какие источники ЛИЧНО проверены юзером (не «бета»). Пока — только telegram: каналы юзер
# сам курировал из своей папки, @tproger подтвердил живым смоуком. Остальные 4 (hn/reddit/
# lobsters/gh_trending) подключены, крутятся, но юзером персонально НЕ провалидированы —
# пульт метит их «β» (бета). Это метаданные доверия, не живой статус: живой 🟢/🔴 считает
# _status_from_out по фактическому улову, а beta — статичный признак отсюда. Расширять по мере
# того, как юзер подтверждает источник вручную.
USER_VERIFIED_SOURCES = {"telegram", "files"}  # files — свои папки юзера, не «бета»

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


def _source_env():
    """Единый ИСТОЧНИК идей для ОБЕИХ кнопок: ручной «Принеси идеи» (cyborg/run.py) и
    автосбор (main тут). Широкий слой (n=SOURCE_N) + телеграм-каналы + живой мозг. БЕЗ
    фильтра «уже видели» — его навешивает только автоцикл (см. _harvest_env). Так у ручного
    и автономного прогона один и тот же источник, а не две разные ленты."""
    active = _active_sources()
    env = {"n": SOURCE_N, "sources": active}
    # Телеграм-креды/каналы — ТОЛЬКО когда telegram реально включён (тумблер в пульте). Иначе
    # env тащил telegram_session даже при выключенной ленте → _collect_locked брал tg-замок (130с
    # таймаут) на прогон, где телеги нет: files-only прогон вис на замке. Нет telegram в active →
    # нет замка → прогон по одним папкам мгновенный (баг тумблеров 2026-07-14).
    if "telegram" in active:
        api_id, api_hash = _load_darbot_tg_creds()
        if api_id and api_hash and os.path.exists(_KIBORG_TG_SESSION):
            env["telegram_channels"] = TELEGRAM_CHANNELS
            env["telegram_api_id"] = api_id
            env["telegram_api_hash"] = api_hash
            env["telegram_session"] = _KIBORG_TG_SESSION
            env["telegram_timeout"] = 90   # 21 канал × 5 постов — глубже фетч, шире таймаут (время не важно)
    if ask_llm.available():
        env["content_llm"] = ask_llm.ask
    d = direction.current()
    if d:
        env["direction"] = d               # руль темы (пусто = без направления, как раньше)
    paths = folders.current()
    if paths:
        env["files_paths"] = paths          # источник-папка активен только когда папки заданы
    return env


def wire_council(env):
    """Впаять СОВЕТ на шаг отбора идей в готовый env (мутирует и возвращает его). ЕДИНЫЙ
    источник истины для ОБЕИХ кнопок — ручной run.py и автосбора _harvest_env, чтобы пути не
    разошлись снова (баг 2026-07-13: совет жил только в ручной кнопке, а фон судил ОДНИМ
    арбитром → «максимум качества» был неполным). llm_chain — цепочка интуиции; orchestra —
    7-модельный оркестр (спит при KIBORG_SLEEP_ORCHESTRA=1). Нет ключей → ключи не появляются,
    отбор мягко падает на одного судью. Дёшево: чтение llm_keys.env, без сети (сеть — при
    голосовании внутри отбора, не тут)."""
    chain = keychain.build_chain()
    if chain:
        env["llm_chain"] = chain
    if not os.environ.get("KIBORG_SLEEP_ORCHESTRA"):
        orch = keychain.orchestra_context()
        if orch:
            env["orchestra"] = orch
    return env


def _harvest_env():
    """env АВТОСБОРА: тот же источник + фильтр «уже видели» (по ID items, не по тексту идей —
    см. seen_items.py) + СОВЕТ на отборе (wire_council — тот же провод, что у ручной кнопки,
    чтобы фон судил взвешенным советом, а не одним арбитром). Флаг «уже видели» ставит ТОЛЬКО
    автоцикл; ручной «Принеси идеи» его не ставит (жмёшь — хочешь идей сейчас, даже если посты
    уже мелькали)."""
    return wire_council({**_source_env(), "filter_seen_items": True})


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
    for name in _active_sources():
        cnt = counts.get(name, 0)
        sources[name] = {"items": cnt, "ok": cnt > 0 and name not in errs,
                         "error": errs.get(name),
                         "beta": name not in USER_VERIFIED_SOURCES}  # β в пульте: юзером не проверен
    return {"checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "degraded": bool(out.get("degraded")), "sources": sources}


def _atomic_write(path, text):
    """Атомарная запись: во временный файл рядом + os.replace — обрыв записи НЕ обрежет файл
    (старый цел до последнего шага). Живой статус/отпечаток источников пишем им."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _persist_status(status):
    """Атомарно пишет живой статус источников для пульта."""
    _atomic_write(STATUS_FILE, json.dumps(status, ensure_ascii=False))


def _source_signature():
    """ДЁШЕВО (без LLM) снять отпечаток источника: тот же HTTP, что делает collect_source,
    но БЕЗ дорогих ideate/rank. Чтобы не гонять Gemini впустую, когда ВСЕ ленты не изменились
    ИЛИ изменились, но всё новое мы уже разбирали раньше (fresh_n==0 — точнее, чем просто хеш).
    Отпечаток покрывает ОБЪЕДИНЕНИЕ активных источников (_active_sources) — иначе смена в
    reddit/lobsters/gh_trending при неизменном HN давала бы ложный gate-пропуск.
    Возвращает (sig|None, degraded, fresh_n|None, status|None, out|None). None-хвосты -> не смогли
    снять. out — сам выхлоп гейт-фетча (items+degraded+...); прогон переиспользует его вместо 2-го фетча.
    status — живой per-source расклад (тот же fetch, что и отпечаток — БЕЗ доп. сети).
    NB: count_fresh — non-mutating, ничего не отмечает виденным (отметка — только в реальном
    прогоне, внутри wiring._run_collect, чтобы не терять сырьё на прогонах, что сами же пропустили)."""
    try:
        # ПОЛНЫЙ env прогона (вкл. telegram-креды), а не голые n/sources — чтобы отпечаток И статус
        # видели ВСЕ 5 источников так же, как реальный прогон. Иначе telegram без кредов в пробе
        # ложно «упал» (no channels), а он в прогоне работает. Цена — один pyrogram-спавн на
        # гейт-проверку (раз в ~30 мин); зато gate ловит и telegram-churn, а статус честен.
        # Под замком tg-сессии (_collect_locked): гейт-проба — отдельный фетч телеги, её тоже
        # сериализуем, иначе проба и внешний прогон могли бы столкнуться на одном .session.
        out = _collect_locked({}, _harvest_env())
    except Exception as e:
        # не молчим: без отпечатка _should_run пустит прогон ВСЛЕПУЮ — покажем причину
        print(f"гейт-проба источника упала ({type(e).__name__}: {e}) — прогон пойдёт без отпечатка")
        return None, False, None, None, None
    items = out.get("items") or []
    titles = [(it.get("title", "") if isinstance(it, dict) else str(it)) for it in items]
    # 5-й элемент — сам `out` гейт-фетча: прогон переиспользует его вместо ВТОРОГО фетча телеги
    # (гейт и cy.run раньше тянули ленту независимо, 2 pyrogram-логина ~90с/тик; см. _run_collect)
    return (_titles_sig(titles), bool(out.get("degraded")),
            seen_items.count_fresh(items), _status_from_out(out), out)


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
    _atomic_write(STATE_FILE, json.dumps({"sig": sig}, ensure_ascii=False))


def council_note(out):
    """Одна честная строка про совещание на отборе: проснулся ли оркестр и кто голосовал.
    Пусто, если отбор судил не совет (нет ключей -> обычный один судья). ЕДИНЫЙ форматтер
    для ОБЕИХ кнопок (harvest._log + run.py) — чтобы история пульта одинаково показывала
    совет и у авто-, и у ручного прогона (раньше фон логировался БЕЗ пометки → выглядел как
    «судил один арбитр», хотя совет уже впаян)."""
    c = out.get("council")
    if not isinstance(c, dict):
        return ""
    live = c.get("live") or []
    who = "+".join(str(x) for x in live) if live else "—"
    woke = "оркестр ПРОСНУЛСЯ" if c.get("woken") else "оркестр спал"
    return f"{woke} · голоса: {who}"


def _degrade_note(out):
    """Строка про ДЕГРАДАЦИЮ прогона для консоли/лога (root #1: показать сбой, а не прятать за
    «доставлено N»). Пусто, если прогон здоров. Источник ушёл в фолбэк (4 захардкоженных
    заголовка) → «источник в фолбэке»; доставка отсеяла болванки при живом ключе → «stub-отсеяно=N»;
    генератор ответил ПЛАТНЫМ фолбэком (muse-spark вместо бесплатной gemini-подписки) → «фолбэк=…»
    (учёт бюджета closerouter автосбора — gemini провисает на TLS ~1/3 прогонов)."""
    flags = []
    if out.get("degraded"):
        flags.append("источник в фолбэке")
    if out.get("dropped_stub"):
        flags.append(f"stub-отсеяно={out['dropped_stub']}")
    if out.get("dropped_dup"):
        flags.append(f"дубликатов={out['dropped_dup']}")
    # провайдер — только когда это ПЛАТНЫЙ фолбэк (muse-spark); gemini=подписка=бесплатно, не флаг
    prov = out.get("provider") or ""
    if prov and prov != "gemini":
        flags.append(f"фолбэк={prov}")
    return " · ".join(flags)


def _log(goal, out):
    os.makedirs(DATA, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    steps = " -> ".join(t.get("organ") for t in out["trace"] if t.get("organ")) or "—"
    r = out.get("result")
    rv = (str(r)[:120] if r is not None else "нет")
    line = f"- [{ts}] «{goal}» → {steps} | {out['deliverable']}={rv}"
    note = council_note(out)
    if note:
        line += f" | совет: {note}"   # тот же хвост, что у ручного прогона — пульт его уже парсит
    dn = _degrade_note(out)
    if dn:
        line += f" | ⚠ {dn}"          # деградация видна в истории пульта, не только в консоли
    line += "\n"
    with open(os.path.join(DATA, "runs.md"), "a", encoding="utf-8") as f:
        f.write(scrub_secrets.scrub_text(line))


def main(argv):
    force = "--force" in argv or "force" in argv           # ручной клик из пульта перебивает гейт
    nums = [a for a in argv if a.isdigit()]
    n = int(nums[0]) if nums else 1
    n = max(1, min(n, 50))  # предохранитель: не больше 50 прогонов за вызов
    goal = "приноси свежие идеи"   # та же цель/цепочка, что у ручной кнопки → deliver в общий инбокс
    env = _harvest_env()
    mode = (f"идеи={ask_llm._MODEL}" if ask_llm.available() else "идеи=stub (ключа нет)") \
        + f" · источники={'+'.join(_active_sources())} (бюджет {SOURCE_N})" + (" · force" if force else "")

    cy = Cyborg(build_organs(), safe_mode=True, k=6)  # k>=6: роутер сурфейсит всю цепь (+readability_gate)
    total, skipped, total_dropped = 0, 0, 0
    for i in range(n):
        # гейт «есть что нового?» — не гоняем Gemini впустую (а) на неизменной ленте ИЛИ
        # (б) на ленте, что перетасовалась, но всё «новое» мы уже разбирали раньше (fresh_n).
        # force (ручной клик) гейт перепрыгивает: юзер просит собрать СЕЙЧАС — и тогда отпечаток
        # даже не снимаем (иначе лишний fetch 31 заголовка ради результата, который всё равно игнорим).
        if force:
            sig, fresh_n, gate_out = None, None, None
        else:
            sig, _degraded, fresh_n, status, gate_out = _source_signature()
            if status:
                _persist_status(status)   # живой статус источников для пульта (даже если прогон пропустим)
        if not _should_run(sig, force, fresh_n):
            skipped += 1
            why = "нет новых items (уже разбирали)" if fresh_n == 0 else "источник не изменился"
            print(f"прогон {i + 1}/{n}: {why} — пропуск (без вызова LLM)")
            continue
        # переиспользуем items гейт-фетча (не тянем телегу второй раз за тик); force / сбой гейта →
        # gate_out=None → _run_collect фетчит сам, как раньше (фолбэк цел)
        run_env = {**env, "prefetched_out": gate_out} if isinstance(gate_out, dict) else env
        out = cy.run(goal, env=run_env)
        r = out.get("result")
        added = r if isinstance(r, int) else 0
        total += added
        total_dropped += int(out.get("dropped_stub") or 0)   # болванки, отсеянные доставкой за тик
        if sig is not None:
            _save_sig(sig)   # запоминаем ленту только после реального прогона
        _log(goal, out)
        dn = _degrade_note(out)
        print(f"прогон {i + 1}/{n}: +{added} свежих идей в инбокс" + (f"  ⚠ {dn}" if dn else ""))

    print(f"\n{mode}")
    line = f"ЗА ВЫЗОВ добавлено в инбокс: {total} | пропущено (лента не менялась): {skipped}"
    if total_dropped:   # шапка выше = конфиг-модель; тут ФАКТ: болванки = ключ есть, но сеть/парс подвели
        line += f" | ⚠ болванок отсеяно (сеть/парс LLM подводили): {total_dropped}"
    print(line)
    inbox_md = os.path.join(_IE_DATA, "inbox.md")
    try:
        import store as _ie_store   # idea_engine/store.py (idea_engine уже в sys.path через wiring)
        open_n = len(_ie_store.Store(os.path.join(_IE_DATA, "state.json"), cap=0).open_ideas())
        print(f"ВСЕГО в инбоксе (открытых идей): {open_n}")
    except Exception:
        pass
    print(f"инбокс (человеку): {inbox_md}")


if __name__ == "__main__":
    main(sys.argv[1:])
