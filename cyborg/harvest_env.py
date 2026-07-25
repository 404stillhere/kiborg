"""Конфигурация источников и совет для прогона (env-сборщики).

Вынесено из монолита harvest.py: одна зона — собрать env прогона (источники, телеграм-креды,
направление, папки, отклонённые) и впаять совет (wire_council). Органы feeds/folders/direction/
keychain/ask_llm/rejected и патчимые константы (SOURCE_N, TELEGRAM_CHANNELS, _KIBORG_TG_SESSION,
_DARBOT_ENV, _load_darbot_tg_creds) читаем через фасад `import harvest`, чтобы патчи в тестах
(harvest.feeds.enabled, harvest.folders.current, harvest.direction.current,
harvest._load_darbot_tg_creds, harvest._KIBORG_TG_SESSION) долетали до живого кода.

ЕДИНЫЙ источник идей для ОБЕИХ кнопок: ручной «Принеси идеи» (cyborg/run.py) и автосбор
(main в harvest_runner.py) делят _source_env / wire_council, чтобы пути не разошлись.
"""

import os


def _active_sources():
    """Источники прогона: включённые в пульте ленты (feeds) + 'files', ЕСЛИ заданы папки
    (иначе files молчал бы холостой ошибкой 'no folders'). Единая точка правды для env /
    статуса пульта / лога. Все ленты выключены и папок нет -> [] (пульт предупреждает)."""
    import harvest

    return harvest.feeds.enabled() + (["files"] if harvest.folders.current() else [])


def _load_darbot_tg_creds():
    """Читает TG_API_ID/TG_API_HASH из .env darbot (read-only, чужой файл не трогаем) — те же
    app-креды, что у бота, приложение Telegram одно и то же. Сессия — СВОЯ копия kiborg
    (cyborg/data/kiborg_tg.session), не тот файл, что живой darbot-бот держит открытым.
    Нет .env/ключей -> (None, None), _telegram сам мягко деградирует (errors, не крашит)."""
    import harvest

    darbot_env = harvest._DARBOT_ENV
    if not os.path.exists(darbot_env):
        return None, None
    vals = {}
    with open(darbot_env, encoding="utf-8") as f:
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
    import harvest

    active = _active_sources()
    # Параметры генерации из пульта (genparams.json). Все 5 — точка настройки юзера: сколько
    # идей генерить (gen_k), сколько оставлять после совета (rank_keep), сколько сырья собирать
    # (source_n), порог читаемости и мин.балл совета. Долетают до wiring_ideate/wiring_council
    # через env.get(...). source_n сюда вшит намеренно (вместо harvest.SOURCE_N из config) —
    # теперь юзер правит его в пульте, а не правит код. config.SOURCE_N=105 остаётся как дефолт.
    gp = harvest.genparams.load()
    env = {
        "n": gp["source_n"],  # collect_source делит бюджет между источниками
        "sources": active,
        "gen_k": gp["gen_k"],  # wiring_ideate._run_ideate
        "rank_keep": gp["rank_keep"],  # wiring_council._run_rank
        "read_min_score": gp["read_min_score"],  # wiring_council._run_readability
        "keep_min_score": gp["keep_min_score"],  # wiring_council._rank_by_council
    }
    # gh_enrich: для каждого репо из trending тянем description из api.github.com (60 req/h без
    # токена). Превращает слепой «owner/repo» в осмысленную карточку — совету есть за что
    # зацепиться. Включаем, только когда gh_trending реально активен (лишние API-запросы ни к чему).
    if "gh_trending" in active:
        env["gh_enrich"] = True
    # hn_show_mix: половина бюджета HN из showstories (Show HN — реальные проекты), половина из
    # topstories (тренды). Топ HN засорён новостями/некрологами; Show HN — чистое проектное топливо.
    if "hn" in active:
        env["hn_show_mix"] = True
    # Телеграм-креды/каналы — ТОЛЬКО когда telegram реально включён (тумблер в пульте). Иначе
    # env тащил telegram_session даже при выключенной ленте → _collect_locked брал tg-замок (130с
    # таймаут) на прогон, где телеги нет: files-only прогон вис на замке. Нет telegram в active →
    # нет замка → прогон по одним папкам мгновенный (баг тумблеров 2026-07-14).
    if "telegram" in active:
        api_id, api_hash = harvest._load_darbot_tg_creds()
        if api_id and api_hash and os.path.exists(harvest._KIBORG_TG_SESSION):
            env["telegram_channels"] = harvest.TELEGRAM_CHANNELS
            env["telegram_api_id"] = api_id
            env["telegram_api_hash"] = api_hash
            env["telegram_session"] = harvest._KIBORG_TG_SESSION
            env["telegram_timeout"] = 90  # 21 канал × 5 постов — глубже фетч, шире таймаут (время не важно)
    if harvest.ask_llm.available():
        env["content_llm"] = harvest.ask_llm.ask
    d = harvest.direction.current()
    if d:
        env["direction"] = d  # руль темы (пусто = без направления, как раньше)
    paths = harvest.folders.current()
    if paths:
        env["files_paths"] = paths  # источник-папка активен только когда папки заданы
    rej = harvest.rejected.recent()
    if rej:
        env["rejected"] = rej  # отклонённые «мусором» — генератор/судья не приносят похожее
    return env


def wire_council(env):
    """Впаять СОВЕТ на шаг отбора идей в готовый env (мутирует и возвращает его). ЕДИНЫЙ
    источник истины для ОБЕИХ кнопок — ручной run.py и автосбора _harvest_env, чтобы пути не
    разошлись снова (баг 2026-07-13: совет жил только в ручной кнопке, а фон судил ОДНИМ
    арбитром → «максимум качества» был неполным). llm_chain — цепочка интуиции; orchestra —
    7-модельный оркестр (спит при KIBORG_SLEEP_ORCHESTRA=1). Нет ключей → ключи не появляются,
    отбор мягко падает на одного судью. Дёшево: чтение llm_keys.env, без сети (сеть — при
    голосовании внутри отбора, не тут)."""
    import harvest

    chain = harvest.keychain.build_chain()
    if chain:
        env["llm_chain"] = chain
    if not os.environ.get("KIBORG_SLEEP_ORCHESTRA"):
        orch = harvest.keychain.orchestra_context()
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
