"""ГЕЙТ «есть что нового?» — дешёвая проверка до запуска LLM.

Вынесено из монолита harvest.py: одна зона — снять отпечаток источника (без LLM), решить,
гонять ли прогон, запомнить отпечаток, персист живой статус. Константы STATE_FILE/STATUS_FILE
(патчатся в тестах) и органы seen_items/_collect_locked/_harvest_env читаем через фасад
`import harvest`, чтобы патчи долетали.
"""

import datetime
import hashlib
import json


def _titles_sig(titles):
    """Отпечаток набора заголовков (порядок не важен, изменение — важно)."""
    return hashlib.sha1("|".join(sorted(titles)).encode("utf-8")).hexdigest()


def _status_from_out(out):
    """Живой per-source статус из выхлопа collect_source (для пульта): сколько items дал
    каждый источник и упал ли он. ok = дал >=1 item и не в partial_errors. Все упали / нет
    сети -> degraded=True, у всех ok=False. Чистая функция (без I/O) — персист делает main()."""
    import harvest

    items = out.get("items") or []
    counts = {}
    for it in items:
        src = it.get("source") if isinstance(it, dict) else None
        if src:
            counts[src] = counts.get(src, 0) + 1
    errs = {}
    for e in out.get("partial_errors") or []:
        errs[str(e).split(":", 1)[0].strip()] = str(e)
    sources = {}
    for name in harvest._active_sources():
        cnt = counts.get(name, 0)
        sources[name] = {
            "items": cnt,
            "ok": cnt > 0 and name not in errs,
            "error": errs.get(name),
            "beta": name not in harvest.USER_VERIFIED_SOURCES,
        }  # β в пульте: юзером не проверен
    return {
        "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "degraded": bool(out.get("degraded")),
        "sources": sources,
    }


def _atomic_write(path, text):
    """Атомарная запись: во временный файл рядом + os.replace — обрыв записи НЕ обрежет файл
    (старый цел до последнего шага). Живой статус/отпечаток источников пишем им."""
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _persist_status(status):
    """Атомарно пишет живой статус источников для пульта."""
    import harvest

    _atomic_write(harvest.STATUS_FILE, json.dumps(status, ensure_ascii=False))


def _source_signature():
    """ДЁШЕВО (без LLM) снять отпечаток источника: тот же HTTP, что делает collect_source,
    но БЕЗ дорогих ideate/rank. Чтобы не гонять генератор впустую, когда ВСЕ ленты не изменились
    ИЛИ изменились, но всё новое мы уже разбирали раньше (fresh_n==0 — точнее, чем просто хеш).
    Отпечаток покрывает ОБЪЕДИНЕНИЕ активных источников (_active_sources) — иначе смена в
    reddit/lobsters/gh_trending при неизменном HN давала бы ложный gate-пропуск.
    Возвращает (sig|None, degraded, fresh_n|None, status|None, out|None). None-хвосты -> не смогли
    снять. out — сам выхлоп гейт-фетча (items+degraded+...); прогон переиспользует его вместо 2-го фетча.
    status — живой per-source расклад (тот же fetch, что и отпечаток — БЕЗ доп. сети).
    NB: count_fresh — non-mutating, ничего не отмечает виденным (отметка — только в реальном
    прогоне, внутри wiring._run_collect, чтобы не терять сырьё на прогонах, что сами же пропустили)."""
    import harvest

    try:
        # ПОЛНЫЙ env прогона (вкл. telegram-креды), а не голые n/sources — чтобы отпечаток И статус
        # видели ВСЕ 5 источников так же, как реальный прогон. Иначе telegram без кредов в пробе
        # ложно «упал» (no channels), а он в прогоне работает. Цена — один pyrogram-спавн на
        # гейт-проверку (раз в ~30 мин); зато gate ловит и telegram-churn, а статус честен.
        # Под замком tg-сессии (_collect_locked): гейт-проба — отдельный фетч телеги, её тоже
        # сериализуем, иначе проба и внешний прогон могли бы столкнуться на одном .session.
        out = harvest._collect_locked({}, harvest._harvest_env())
    except Exception as e:
        # не молчим: без отпечатка _should_run пустит прогон ВСЛЕПУЮ — покажем причину
        print(f"гейт-проба источника упала ({type(e).__name__}: {e}) — прогон пойдёт без отпечатка")
        return None, False, None, None, None
    items = out.get("items") or []
    titles = [(it.get("title", "") if isinstance(it, dict) else str(it)) for it in items]
    # 5-й элемент — сам `out` гейт-фетча: прогон переиспользует его вместо ВТОРОГО фетча телеги
    # (гейт и cy.run раньше тянули ленту независимо, 2 pyrogram-логина ~90с/тик; см. _run_collect)
    return (
        _titles_sig(titles),
        bool(out.get("degraded")),
        harvest.seen_items.count_fresh(items),
        _status_from_out(out),
        out,
    )


def _last_sig():
    import harvest

    try:
        with open(harvest.STATE_FILE, encoding="utf-8") as f:
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
    import harvest

    _atomic_write(harvest.STATE_FILE, json.dumps({"sig": sig}, ensure_ascii=False))
