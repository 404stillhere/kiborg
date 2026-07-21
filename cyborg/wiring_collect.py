"""ГЛАЗА: сбор внешнего сырья (collect_source) под замком телеграм-сессии.

Вынесено из монолита wiring.py: органы collect_source/state_lock/scrub_secrets и
патчимая константа _TG_LOCK_TIMEOUT читаются через фасад `import wiring`, чтобы
патч `wiring.collect_source.run = mock` / `wiring._TG_LOCK_TIMEOUT = ...` в тестах
долетал до живого кода (test_wiring проверяет именно это).
"""

import os
import time


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
    lock_path = session_path + ".lock"
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
        with wiring.state_lock(sess, timeout=wiring._TG_LOCK_TIMEOUT, poll=0.2) as held:
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


def _run_collect(inputs, env):
    # ВАЖНО: раньше env игнорировался (жёстко n=8/source=hn) — расширение харвеста
    # (SOURCE_N) реально не долетало до сборщика в живом прогоне, только до gate-проверки
    # в _source_signature (та звала collect_source напрямую). Теперь настройки прокидываются.
    import wiring

    env = env if isinstance(env, dict) else {}
    # переиспользуем фетч гейта, если он уже сходил в источник ЭТИМ тиком (harvest кладёт
    # prefetched_out) — не тянем телегу второй раз за тик (~90с/лишний pyrogram-логин). Нет /
    # невалидно (force / сбой гейта / ручной прогон run.py) → фетчим сами, как раньше.
    pf = env.get("prefetched_out")
    if isinstance(pf, dict) and pf.get("items") is not None:
        return pf
    e = {"n": env.get("n", 8), "source": env.get("source", "hn")}
    if env.get("sources") is not None:
        e["sources"] = env["sources"]  # пробрасываем И пустой список: пусто = «нет источников»,
        #                                 collect_source честно вернёт пусто+degraded, не дефолт hn (D7)
    if env.get("timeout"):
        e["timeout"] = env["timeout"]
    # keyed/конфиг-источники читают свои данные из env по своим ключам — их тоже надо ПРОКИНУТЬ,
    # иначе источник в списке sources есть, а данных для него нет → тихо падает в фолбэк/partial_errors.
    # telegram: креды/каналы. files: files_paths (папки-источник) — БЕЗ него _files даёт «no folders
    # configured», весь прогон уходит в 4 захардкодированных заголовка и degraded=True, а папка юзера НЕ
    # читается (баг 2026-07-15: files_paths забыли добавить сюда при вводе источника-папки).
    for k in (
        "telegram_channels",
        "telegram_api_id",
        "telegram_api_hash",
        "telegram_session",
        "telegram_python",
        "telegram_timeout",
        "files_paths",
    ):
        if env.get(k) is not None:
            e[k] = env[k]
    # Глаза ТОЛЬКО смотрят — приносят всё, что увидели, без фильтра «уже видели». Помнить,
    # что уже обдумывали, — работа Мозга (см. _run_ideate): фильтр переехал туда 2026-07-13,
    # чтобы метафора не врала (смотреть ≠ помнить).
    out = _collect_locked(inputs, e)  # под замком tg-сессии, если телеграм в игре
    # ЗАЩИТА ОТ УТЕЧКИ СЕКРЕТА В ПРОМПТ (2026-07-15): файл-источник может принести секрет в
    # ЗАГОЛОВКЕ (собственный фильтр _files неполон — пропускал напр. AQ.-ключ из gitignored
    # gemini.md). Заголовок уходит в ПРОМПТ генератора → к LLM-провайдеру. scrub downstream
    # (перед deliver) ПОЗДНО — промпт уже ушёл. Чистим заголовки ЗДЕСЬ, до генерации: scrub_secrets
    # ловит форматы, что _FILES_SECRET_LINE пропустил (проверено: AQ.-ключ → [REDACTED]).
    if isinstance(out, dict) and isinstance(out.get("items"), list):
        for it in out["items"]:
            if isinstance(it, dict) and isinstance(it.get("title"), str):
                it["title"] = wiring.scrub_secrets.scrub_text(it["title"])
    return out
