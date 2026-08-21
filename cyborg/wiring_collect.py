"""ГЛАЗА: сбор внешнего сырья (collect_source) под замком телеграм-сессии.

Вынесено из монолита wiring.py: органы collect_source/state_lock/scrub_secrets и
патчимая константа _TG_LOCK_TIMEOUT читаются через фасад `import wiring`, чтобы
патч `wiring.collect_source.run = mock` / `wiring._TG_LOCK_TIMEOUT = ...` в тестах
долетал до живого кода (test_wiring проверяет именно это).
"""

import os
import time

import config


def _remove_stale_lock(session_path, max_age_seconds):
    """Снести lock-файл tg-сессии, если он «зависший» (старше max_age_seconds).

    После аварийного падения процесса (kill -9 / OOM / power loss) lock-файл
    `<session_path>.lock` остаётся на диске. Каждый следующий прогон честно ждёт
    полный TG_LOCK_TIMEOUT (130с), прежде чем state_lock решит «прошли без лока».
    Если lock старше порога — он гарантированно чужой труп (живой прогон телеграма
    укладывается в фетч ~90с << порога 30мин), и мы его сносим ПЕРЕД попыткой захвата.

    Имя lock-файла формирует ТА ЖЕ логика, что и frozen store.state_lock: `path + ".lock"`.
    Логику дублируем (НЕ импортируем из store.py), потому что store.py — frozen core
    и публично не раскрывает схему имен.

    Свежий lock (живой конкурент) НЕ трогаем: mtime < порога → нормальная конкуренция,
    state_lock честно подождёт освобождения. Файла нет → ничего не делаем.
    Возвращает True, если удалили протухший lock (для тестов/логов).
    """
    if not session_path:
        return False
    lock_path = session_path + config.TG_LOCK_SUFFIX
    try:
        st = os.stat(lock_path)
    except OSError:
        return False  # файла нет — нормально, нечего сносить
    age = time.time() - st.st_mtime
    if age < max_age_seconds:
        return False  # свежий —可能是 живой конкурент, не лезем
    try:
        os.remove(lock_path)
    except OSError:
        return False  # уже ушёл (гонка с другим чистильщиком / нет прав) — не наша проблема
    print(
        f"[stale-lock] удалён зависший lock {lock_path} "
        f"(age: {int(age // 60)} мин > {int(max_age_seconds // 60)} мин порога)"
    )
    return True


def _collect_locked(inputs, env):
    """collect_source.run под замком tg-сесии, когда телеграм в игре (иначе — как есть)."""
    import wiring

    sess = (env or {}).get("telegram_session")
    if sess:
        # Сначала снесём зависший lock (если крашнулся прошлый процесс и оставил труп).
        # Без этого — ждём 130с таймаута; с этим — сразу O_EXCL-захват. Свежий lock не трогаем.
        _remove_stale_lock(sess, wiring._STALE_LOCK_MAX_AGE)
        with wiring.state_lock(sess, timeout=wiring._TG_LOCK_TIMEOUT, poll=wiring._TG_LOCK_POLL_INTERVAL) as held:
            if not held:
                print(
                    f"[warn] state_lock timeout ({wiring._TG_LOCK_TIMEOUT}s) на {sess} — "
                    f"прошли без лока (возможна гонка write)"
                )
                # Зафиксировать для пульта: /api/health покажет recent_timeouts за час.
                # Живой конкурент держал лок >130с — администратор должен это видеть.
                import lock_monitor  # noqa: E402  (ленивый: serve.py и wiring оба на path)

                lock_monitor.record_timeout()
            return wiring.collect_source.run(inputs, env)
    return wiring.collect_source.run(inputs, env)


def _scrub_and_cross_dedup(out):
    """Общий шаг обработки ЛЮБОГО выхлопа сбора: scrub секретов + кросс-дедуп.

    Раньше работал только на пути собственного фетча: ранний return из
    prefetched_out (выхлоп гейта, автономный harvest-путь) обходил обе защиты —
    секрет из файла-источника уходил в промпт на ГЛАВНОМ пути, а чистился только
    на ручном (council 2026-08-17, находка #4). Идемпотентен: чистое не трогает.
    """
    import wiring

    if isinstance(out, dict) and isinstance(out.get("items"), list):
        # ЗАЩИТА ОТ УТЕЧКИ СЕКРЕТА В ПРОМПТ (2026-07-15): файл-источник может принести
        # секрет в ЗАГОЛОВКЕ ИЛИ КОНТЕКСТЕ (собственный фильтр _files неполон — пропускал
        # напр. AQ.-ключ из gitignored gemini.md). Оба поля уходят в ПРОМПТ генератора →
        # к LLM-провайдеру. scrub downstream (перед deliver) ПОЗДНО — промпт уже ушёл.
        # Чистим ОБА поля до генерации: scrub_secrets ловит форматы, что _FILES_SECRET_LINE
        # пропустил.
        for it in out["items"]:
            if isinstance(it, dict):
                for field in ("title", "context"):
                    if isinstance(it.get(field), str):
                        it[field] = wiring.scrub_secrets.scrub_text(it[field])
        # КРОСС-ДЕДУП (2026-07-23): один пост может прийти с HN (item id) и Lobsters
        # (short_id) — в seen_items это два разных ключа, оба проходят → LLM тратится на
        # две похожие идеи. Уберём дубли ВНУТРИ прогона по нормализованному заголовку
        # (первое вхождение выигрывает), до ideate. Чистая функция (нет персиста),
        # строгая (точное совпадение, не Jaccard).
        out["items"] = wiring.seen_items.cross_dedup(out["items"])
    return out


def _run_collect(inputs, env):
    # ВАЖНО: раньше env игнорировался (жёстко n=8/source=hn) — расширение харвеста
    # (SOURCE_N) реально не долетало до сборщика в живом прогоне, только до gate-проверки
    # в _source_signature (та звала collect_source напрямую). Теперь настройки прокидываются.

    env = env if isinstance(env, dict) else {}
    # переиспользуем фетч гейта, если он уже сходил в источник ЭТИМ тиком (harvest кладёт
    # prefetched_out) — не тянем телегу второй раз за тик (~90с/лишний pyrogram-логин).
    # ФЕТЧ пропускаем, но ОБРАБОТКУ — нет: scrub секретов и кросс-дедуп обязаны работать
    # и на автономном пути (раньше ранний return обходил их — council 2026-08-17, #4).
    pf = env.get("prefetched_out")
    if isinstance(pf, dict) and pf.get("items") is not None:
        return _scrub_and_cross_dedup(pf)
    e = {"n": env.get("n", config.COLLECT_DEFAULT_N), "source": env.get("source", config.COLLECT_DEFAULT_SOURCE)}
    if env.get("sources") is not None:
        e["sources"] = env["sources"]  # пробрасываем И пустой список: пусто = «нет источников»,
        #                                 collect_source честно вернёт пусто+degraded, не дефолт hn (D7)
    if env.get("timeout"):
        e["timeout"] = env["timeout"]
    # keyed/конфиг-источники читают свои данные из env по своим ключам — их тоже надо ПРОКИНУТЬ,
    # иначе источник в списке sources есть, а данных для него нет → честно попадает в partial_errors.
    # telegram: креды/каналы. files: files_paths (папки-источник) — БЕЗ него _files даёт «no folders
    # configured», прогон честно вернёт пустое сырьё и degraded=True, а папка юзера НЕ читается
    # (баг 2026-07-15: files_paths забыли добавить сюда при вводе источника-папки).
    for k in (
        "telegram_channels",
        "telegram_api_id",
        "telegram_api_hash",
        "telegram_session",
        "telegram_python",
        "telegram_timeout",
        "self_path",
        "files_paths",
        # Качество публичных лент. harvest_gate передаёт весь env, а ручной run.py идёт
        # сюда без prefetched_out: без этого явного проброса ручной запуск терял Show HN
        # и описания GitHub-репозиториев, хотя фон видел их правильно.
        "hn_show_mix",
        "gh_enrich",
        "gh_enrich_limit",
    ):
        if env.get(k) is not None:
            e[k] = env[k]
    # Глаза ТОЛЬКО смотрят — приносят всё, что увидели, без фильтра «уже видели». Помнить,
    # что уже обдумывали, — работа Мозга (см. _run_ideate): фильтр переехал туда 2026-07-13,
    # чтобы метафора не врала (смотреть ≠ помнить).
    out = _collect_locked(inputs, e)  # под замком tg-сессии, если телеграм в игре
    return _scrub_and_cross_dedup(out)
