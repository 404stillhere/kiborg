---
model: claude-opus-4-8 (для правок источников/фиксов — opus + high; для тестов/доков — sonnet + medium)
effort: high
tags: [kiborg, sources, collect_source, deep-study, reddit-403, gh_trending, telegram, harvest, tests, freeze]
date: 2026-07-12
status: open (карта для углублённой работы)
---

# Kiborg — 5 источников идей: карта для углублённого изучения и работы

Проект: **M:/projects/kiborg**. Это handoff для СЛЕДУЮЩЕГО чата, где будем разбирать
5 источников идей вглубь и дорабатывать. Всё ниже сверено по РЕАЛЬНОМУ коду (file:line),
не по памяти. Собрано параллельным разбором 8 читателей + ручная дочитка кода трёх источников.

> Смежный, но ДРУГОЙ handoff: `handoff_2026-07-12_add-idea-sources.md` — про ДОБАВЛЕНИЕ
> НОВЫХ источников (Product Hunt и т.п., выбираем вместе, гейт юзера). ЭТОТ — про
> ИЗУЧЕНИЕ и доработку 5 УЖЕ существующих. Не путать.

## Заморозка (важно перед любой правкой)
- **Ядро `idea_engine/` и `cyborg/` (логика) ЗАМОРОЖЕНО** — только читать. Сам орган сбора
  `idea_engine/organs/collect_source.py` — это ядро. Почти все улучшения источников (фикс
  reddit, ретраи, пагинация, богаче заголовки) ТРОГАЮТ этот файл → **гейт юзера / со-создание**.
- **Свободно правлю** (не ядро): `cyborg/harvest.py` (конфиг накопителя — SOURCE_N, SOURCES,
  TELEGRAM_CHANNELS, статус), `panel/`, тесты (`*/tests/`), доки (`*/README.md`), тулинг.
- Изучать (читать, мерить, прогонять смоук) можно ВСЁ свободно. Гейт — только на запись в ядро.

## Общая картина сбора (как крутится один прогон)
```
env{n, sources, timeout, telegram_*} 
  → collect_source.run()                                    [idea_engine/organs/collect_source.py:167]
      → per_n = ceil(n / кол-во источников)                 [:173]  (n=30, 5 ист. → per_n=6 каждому)
      → цикл по SOURCES: fn(per_n, timeout, env)            [:176-189]
      → плоская склейка в один список, тег it["source"]     [:186-189]  (дедупа ВНУТРИ органа нет)
      → всё упало → _FALLBACK[:n], degraded=True            [:192-196]  (НЕ ключ "error" — иначе блок)
      → часть упала → out["partial_errors"]=[...]           [:199-200]
  → downstream дедуп seen_items (если filter_seen_items)    [cyborg/wiring.py:49-51]  ключ "source:id"
  → ideate (Gemini) → rank_ideas (судья) → scrub → sink (копилка/инбокс)
```
Накопитель `harvest.py` кладёт `sources=SOURCES` (5 шт), `n=SOURCE_N=30`, telegram-креды и
`filter_seen_items=True` в env (`_harvest_env` [harvest.py:89-102]). Интерактивный «приноси
идеи» из пульта флаг дедупа НЕ ставит.

---

## Источник 1 — HN (Hacker News)   [_hn, collect_source.py:43-52]
- **Как фетчит:** ДВА хода. (1) `HN_TOP` = `hacker-news.firebaseio.com/v0/topstories.json` [:23] →
  список id, берёт `[:n]` [:44]. (2) на КАЖДЫЙ id — `HN_ITEM.../item/{}.json` [:24, :47]. Т.е.
  per_n items = **1 + per_n HTTP-запросов** (при per_n=6 → 7 запросов). Самый «дорогой» по числу
  запросов источник. stdlib `urllib` + общий хелпер `_get` (json.loads) [:38-40].
- **item:** `{title, url: it.get("url",""), id: it.get("id")}` [:49]. Фильтр — пропуск без title [:48].
- **auth:** без ключа, БЕЗ User-Agent (Firebase public API). Пусто → `ValueError("hn returned empty")` [:51].
- **Известные проблемы:** N+1 запросов = самый медленный; topstories меняется медленно (часы) →
  те же items → дедуп режет → **холостые прогоны** (это КОРЕНЬ «копилка застряла на 6», описан в
  harvest.py:40-44 — из-за него и подняли SOURCE_N до 30 и добавили 4 источника). url пустой у
  Ask/Show HN self-постов.
- **Копать:** (а) брать не topstories, а beststories/newstories для свежести; (б) параллелить N+1
  item-фетчи (сейчас последовательно, per_n GET'ов подряд — медленно на timeout=8); (в) тянуть
  score/descendants для сигнала ideate; (г) HN Algolia API (`hn.algolia.com/api/v1/search`) как
  альтернатива — один запрос вместо N+1, есть поиск/фильтры.

## Источник 2 — Reddit (r/SideProject)   [_reddit, collect_source.py:55-68]  ⚠️ СЕЙЧАС ЛЕЖИТ
- **Как фетчит:** `REDDIT_TOP` = `reddit.com/r/SideProject/top.json?t=day&limit={n}` [:25, :57].
  Один GET с обязательным User-Agent `_UA` [:28, :57] (без UA reddit даёт 429). Проход по
  `data["data"]["children"][:n]` [:60].
- **item:** `{title, url: d["url"] или "reddit.com"+permalink, id: d["id"]}` [:64-65]. id — base36.
- **auth:** без ключа, только статический UA. Пусто → `ValueError` [:67].
- **⚠️ ГЛАВНАЯ ПРОБЛЕМА — 403 IP-блок:** живой статус на 2026-07-12 20:01 → `reddit 0 🔴 "HTTP Error
  403: Blocked"`. Reddit блочит IP этого хоста. В коде НЕТ обработки 403 (общий except → partial_errors,
  источник тихо даёт 0). Комментарий кода говорит только про 429, реальная стена — 403 по IP.
- **Копать (это ПРИОРИТЕТ №1 — единственный мёртвый из 5):**
  - Проверить живой ответ с текущего IP; попробовать `old.reddit.com/r/SideProject/top.json`,
    или RSS `r/SideProject/top/.rss?t=day`, или маршрут через уже поднятый прокси xray (:10809).
  - Возможно, авторизованный OAuth-app (script-type app) обходит IP-блок — но это токен = гейт юзера.
  - Расширить на смежные сабы (r/startups, r/indiehackers, r/EntrepreneurRideAlong) — больше churn.
  - `html.unescape` заголовков (сущности `&amp;`), параметризовать `t=` (day/week).

## Источник 3 — Lobsters   [_lobsters, collect_source.py:71-81]
- **Как фетчит:** `LOBSTERS_HOT` = `lobste.rs/hottest.json` [:26]. Один GET, JSON-список, `[:n]` [:74].
- **item:** `{title, url: it["url"] или comments_url, id: it["short_id"]}` [:77-78]. Без ключа, без UA.
  Пусто → `ValueError` [:80]. Самый простой источник.
- **Известные проблемы:** hottest меняется медленно (тот же дедуп-риск, что HN/reddit); один
  endpoint (нельзя выбрать newest/тег); нет пагинации.
- **Копать:** параметризовать (newest.json / tag/*.json по тематике tech/AI); при желании — фильтр
  по тегам, чтобы сырьё было ближе к теме киборга.

## Источник 4 — GitHub Trending   [_gh_trending, collect_source.py:84-100]
- **Как фетчит:** `GH_TRENDING` = `github.com/trending` [:27] — HTML-СКРЕЙП (офиц. API нет). UA
  спуфнут под браузер `Mozilla/5.0(...)` [:87]. Парс regex: блоки `<h2 class~lh-condensed>` [:90] →
  первый `href="/owner/repo"` [:93]. JSON не парсится.
- **item:** `{title: "owner/repo", url, id: "owner/repo"}` [:96-97] — title это ТОЛЬКО слаг, без
  описания/языка/звёзд. Пусто → `ValueError("no repos parsed")` [:99].
- **Известные проблемы:** хрупкий regex (редизайн GitHub → тихий degrade); бедный сигнал для ideate
  (голый owner/repo, LLM не видит о чём репо); regex берёт ПЕРВЫЙ href в h2 (риск попасть на
  sponsor/topic-ссылку); нет параметров (since/язык).
- **Копать:** обогатить title (описание `p.col-9`, язык `programmingLanguage`, звёзды); альтернатива
  скрейпу — GitHub Search API `sort=stars` (устойчивее); параметры `since=weekly` / `/trending/<lang>`;
  ужесточить извлечение репо-ссылки. **Смоук не прогонялся** — стоит подтвердить, что разметка ещё
  матчится (`python -c "..."` с source=gh_trending в изоляции).

## Источник 5 — Telegram-каналы   [_telegram, collect_source.py:114-155]  🔑 единственный с кредами
- **Как фетчит:** через ЛИЧНЫЙ ТГ-аккаунт (pyrogram), а pyrogram НЕ stdlib → запуск ОТДЕЛЬНЫМ
  процессом на venv darbot: `subprocess.run([darbot_python, cyborg/organs_vendored/collect_tg_news.py,
  "--rpc"], input=payload)` [:107-111, :140-141]. Орган вендорен из darbot (EXTRACT_ORGAN).
- **Выборка каналов:** `random.sample` каналов ДО фетча [:129-130] — из 21 канала берёт ~per_n
  случайных, чтобы (а) не долбить все 21 каждый прогон, (б) «хвост» списка попадал в выдачу на
  следующих прогонах (ротация по времени). `limit_per_channel = n // кол-во каналов` [:131].
- **item:** `{title: первая строка text [:200 симв], url, id: "channel:id"}` [:147-151]. Возврат
  `items[:n]` [:155].
- **auth (креды резолвит harvest, НЕ орган):** `_load_darbot_tg_creds` [harvest.py:71-86] читает
  `TG_API_ID/HASH` из `darbot/.env` (read-only), сессия — СВОЯ копия `cyborg/data/kiborg_tg.session`
  (не файл живого darbot-бота). Нет кредов/сессии → telegram сам мягко выключается (ValueError,
  partial_errors, прогон не рвёт). Каналы: 21 шт (@tproger + 20 AI) [harvest.py:58-65].
- **Известные проблемы:** зависит от чужого venv (darbot) + чужого .env + своей сессии; спавн
  процесса дорог (timeout 25с); `random.sample` → недетерминированный охват; title = только первая
  строка обрезана до 200 (теряется контекст многострочного поста).
- **Копать:** проверить стабильность сессии `kiborg_tg.session` (не протухла ли); брать не только
  первую строку (или умнее — первые 2-3 строки/заголовок поста); подтвердить, что 21 канал живой
  (какие-то могли переименоваться); рассмотреть свой venv с pyrogram вместо зависимости от darbot.

---

## Слой слияния (бюджет / склейка / дедуп)   [collect_source.run + wiring + seen_items]
- **Деление бюджета:** `per_n = max(1, ceil(n/len))` [:173]. Из-за ceil суммарный потолок может
  ПРЕВЫСИТЬ n (n=8, 5 ист. → per_n=2 → до 10 items). Упавший/неизвестный источник всё равно «съедает»
  свою долю делителя → при частичных сбоях реальный улов заметно меньше n.
- **Склейка:** плоская конкатенация в порядке SOURCES, `it.setdefault("source", name)` [:186-189].
  Дедупа МЕЖДУ источниками в органе НЕТ (по дизайну, докстринг :11-12).
- **Дедуп seen_items:** downstream в `wiring._run_collect` [:49-51], только при `filter_seen_items`
  (harvest ставит, интерактив — нет). Ключ СОСТАВНОЙ `"source:id"` [seen_items.py:18-24]; id
  None/пустой → item «всегда свежий». **Нюанс:** одинаковая новость из hn и reddit НЕ схлопнется на
  seen_items (ключи разные) — текстовый дедуп только позже, в stash после ideate (Jaccard). Значит
  один и тот же заголовок из двух лент может дойти до ДОРОГОГО ideate дважды — проверить, есть ли дыра.
- **env['source'] single-режим:** пустой список `sources=[]` (falsy) молча уходит в фолбэк на один
  'hn' [:172] — проверить, не приходит ли такой env из панели, теряя 4 источника незаметно.

## Живой статус источников (🟢/🔴)   [harvest → serve → panel]
- **Пишет ТОЛЬКО harvest на авто-прогонах:** `_source_signature` [harvest.py:137-159] делает полный
  fetch (тот же env, вкл. telegram) → `_status_from_out` [:110-129] считает items/ok/error →
  `_persist_status` [:132-134] атомарно в `cyborg/data/source_status.json`. main() пишет статус даже
  если прогон пропущен гейтом [:217-220].
- **Читает:** serve `_read_source_status` [serve.py:165-173] → `/api/state.sources` [:268]; панель
  `renderSources` [index.html:747-763], опрос каждые 5с.
- **Минор (подтверждён кодом):** force-кнопка → `harvest.py 1 --force`, а force-ветка [:215-216]
  ставит sig/fresh_n=None и НЕ зовёт `_persist_status`. Значит **клик «собрать сейчас» статус не
  обновляет** — чипы держат последнюю АВТО-проверку (видно по метке «проверка HH:MM»). Сознательно
  (избегали лишнего fetch). Чинить, только если юзеру важно обновление по клику — варианты в
  open_threads разбора (взять статус из самого force-прогона / отдельная проба / пометка в UI).

## Тесты и доки — покрытие и дыры
- **Где:** парсинг источников — `idea_engine/tests/test_collect_source.py` (15 тестов); гейт/статус —
  `cyborg/tests/test_harvest.py`. Ни один README не зовёт test_collect_source явно.
- **Главная дыра:** **HN — единственный источник БЕЗ happy-path теста** (покрыт только веткой degrade
  при обрыве сети). Остальные 4 имеют тест разбора.
- **Ещё дыры:** url-fallback ветки (reddit permalink [:64], lobsters comments_url [:77]) мертвы для
  тестов; деление бюджета per_n [:173] НЕ проверяется (тест `..._split_budget` по имени обещает, но
  бюджет не ассертит — переименовать/дополнить); внутренняя логика telegram (random.sample,
  limit_per_channel, обрезка 200, items[:n], TimeoutExpired) не тестируется.
- **Док-долг:** `idea_engine/README.md` таблица органов [:26] стале (`source/n/timeout`, демо single
  hn); ни один README не документирует покрытие collect_source. **Тесты и доки СВОБОДНЫ к правке**
  (не ядро) — это низко висящий фрукт для углублённой сессии.

---

## Приоритетный список работ (ранжирован; [ядро=гейт] / [свободно])
1. **[ядро=гейт] Reddit 403** — вернуть 5/5 живых. old.reddit / RSS / прокси xray. Приоритет №1.
2. **[свободно] Тесты-дыры** — HN happy-path, честный тест per_n, url-fallback'и, telegram-внутрянка.
   Не трогает ядро, чистая польза, гнать через `python run_tests.py`.
3. **[свободно] Док-долг** — обновить idea_engine/README таблицу, добавить секцию покрытия источников.
4. **[ядро=гейт] Богаче заголовки** — gh_trending (описание/язык/звёзды) + telegram (не только 1-я
   строка). Слабый сигнал для ideate — самый большой рычаг качества идей.
5. **[ядро=гейт] Холостые прогоны** — hn/reddit/lobsters меняются медленно. Параметры окна/пагинация/
   свежие endpoint'ы (beste/new HN, newest lobsters, since=weekly gh).
6. **[свободно, panel/cyborg] Минор force-статуса** — если важно обновление по клику.
7. **[изучить] seen_items составной ключ** — убедиться, что все 5 отдают стабильный непустой id
   (иначе раздув копилки); проверить дубль hn↔reddit до ideate.

## Ключевые файлы
- Сбор (ЯДРО, гейт): `idea_engine/organs/collect_source.py` (все 5 в `_SOURCES` [:158-164]).
- Вендор ТГ: `cyborg/organs_vendored/collect_tg_news.py` (копия darbot/organ.py).
- Накопитель (СВОБОДНО): `cyborg/harvest.py` (SOURCE_N=30, SOURCES, TELEGRAM_CHANNELS, статус,
  креды из darbot/.env).
- Проводка/дедуп: `cyborg/wiring.py` (_run_collect), `cyborg/seen_items.py`.
- Пульт: `panel/serve.py` (_read_source_status), `panel/index.html` (renderSources).
- Тесты: `idea_engine/tests/test_collect_source.py`, `cyborg/tests/test_harvest.py`.
- Данные: `cyborg/data/{source_status.json, seen_items.json, idea_stash.md, runs.md}`.

## Запуск / проверка
- Один прогон накопителя: `python M:/projects/kiborg/cyborg/harvest.py`
- Смоук одного источника в изоляции: `python -c "import sys; sys.path.insert(0,'M:/projects/kiborg/idea_engine'); from organs import collect_source; import json; print(json.dumps(collect_source.run({}, {'source':'gh_trending','n':6}), ensure_ascii=False, indent=2))"`
- Все тесты: `python M:/projects/kiborg/run_tests.py` (НЕ голый pytest из корня — соврёт из-за коллизии имён run.py/store.py).
- Пульт: `python M:/projects/kiborg/panel/serve.py` → 127.0.0.1:8737.

## NB
- git у проекта НЕТ → откат через `.bak` с датой-временем.
- Reddit заблокирован по IP этого хоста (403) — это не баг кода, это внешняя стена.
- Живой статус на момент сборки: hn 6🟢 · reddit 0🔴(403) · lobsters 6🟢 · gh_trending 6🟢 · telegram 4🟢.
- Разбор собран workflow'ом: 5 читателей прошли, 3 (hn/lobsters/telegram) упали на схеме
  StructuredOutput — их разделы дописаны вручную по коду collect_source.py, факты те же, сверены.
