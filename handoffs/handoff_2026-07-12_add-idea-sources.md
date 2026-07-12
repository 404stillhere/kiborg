---
model: claude-opus-4-8 (или sonnet — правки средней сложности)
effort: medium
tags: [kiborg, idea-engine, collect_source, sources, harvest, reddit, github-trending, lobsters, product-hunt]
date: 2026-07-12
---

# Kiborg — добавить НОВЫЕ источники идей (сейчас только Hacker News)

Проект: **M:/projects/kiborg**. Цель нового чата: расширить сбор сырья для идей —
сейчас единственный источник Hacker News. Кандидаты: Reddit, Lobsters, GitHub Trending, Product Hunt.

## Что сейчас (сверено с кодом 2026-07-12)

**Единственный источник — Hacker News.** Орган `idea_engine/organs/collect_source.py`:
- `run(inputs, env)` читает из env: `n` (сколько заголовков, дефолт 8), `timeout` (8с), `source` (дефолт `"hn"`).
- `source=="hn"` → HN topstories API (`hacker-news.firebaseio.com`, БЕЗ ключа, urllib+json).
- Любой ДРУГОЙ `source` → отдаёт 4 запасных заголовка `_FALLBACK`, `degraded=True`. Т.е. заглушка под
  github-trending/research в докстринге ЕСТЬ, но не реализована — неизвестный источник = сэмпл.
- Контракт «degrade = успех с резервом, НЕ краш»: при сбое возвращает `degraded=True`+fallback,
  НИКОГДА ключ `"error"` (иначе киборг пометит источник сдохшим и заблокирует). **Это сохранить.**

**Драйвер копилки — `cyborg/harvest.py`:**
- `SOURCE_N = 30` (расширил топ HN 8→30 конфигом). `_harvest_env()` кладёт в env только `n=SOURCE_N`
  (source по дефолту = "hn"). `content_llm` = Gemini, если ключ есть.
- `_source_signature()` — дешёвый отпечаток ленты БЕЗ LLM (тот же HTTP, чтоб не гонять Gemini
  впустую на неизменной ленте). **Хардкодит `source:"hn"`** — при добавлении источников обновить,
  чтоб отпечаток покрывал ВСЕ источники (объединение), иначе гейт-пропуск будет врать.
- Конвейер: collect_source → ideate → rank_ideas → scrub_secrets → **stash** (копилка без потолка).
- Дедуп (Jaccard ≥0.6) уже режет повторы по ЛЮБОМУ входу → межисточниковые дубли обработаны бесплатно.
- scrub уже в цепочке. **Менять конвейер НЕ нужно** — только источник(и) на входе.

**Тесты:** `idea_engine/tests/test_collect_source.py`, `cyborg/tests/test_harvest.py`. Базлайн 91 зелёный.

## ⚠️ Заморозка ядра — тут развилка (решить в начале чата)
`collect_source.py` — в `idea_engine/` = ЗАМОРОЖЕННОЕ ЯДРО. Юзер попросил добавить источники →
это и есть разрешение тронуть collect_source (в рамках «источники»). Два пути:
- **(А, рекомендую)** Расширить сам `collect_source.py`: добавить фетчеры `_reddit`/`_lobsters`/`_gh`,
  диспетчер по `source`, поддержку списка источников. Минимум новых файлов, орган остаётся один.
- **(Б)** Не трогать ядро: новый орган-коллектор в моей зоне (`cyborg/` или отдельный файл) +
  проводка в wiring. Ядро цело, но появляется второй коллектор — чуть больше клея.

## Развилка А/Б/В по ИСТОЧНИКАМ (отложенная «малина» — решить с юзером)
Все замеры «нужен ли ключ» — [F] по публичным API:
- **Reddit** — публичный `.json` на сабреддите (`reddit.com/r/SideProject/top.json?t=day`), БЕЗ ключа.
  ⚠️ требует свой `User-Agent` в заголовке, иначе 429. Богатый на pet-project идеи.
- **Lobsters** — `https://lobste.rs/hottest.json`, БЕЗ ключа, чистый JSON. Технее, меньше шума.
- **GitHub Trending** — официального API НЕТ. Либо HTML-скрейп `github.com/trending` (stdlib
  `html.parser`/regex), либо неофициальное зеркало-API. Даёт «что щас делают», а не «обсуждают».
- **Product Hunt** — GraphQL API, НУЖЕН токен-ключ → это гейт на юзера, отложить.

**Рекомендация:** начать с бесключевых **Reddit + Lobsters + GitHub Trending**, Product Hunt отложить.
Стратегия объединения: **мержить все источники за прогон** (разнообразнее сырьё + больше churn для
гейта), а не ротация по одному. Отпечаток `_source_signature()` тогда снимает объединение.

## Ограничения (не споткнуться)
- **Только stdlib** — нет requests/feedparser. urllib.request + json; для GitHub Trending — html.parser/regex.
- Reddit без `User-Agent` → 429. Ставить заголовок.
- Держать контракт degrade (см. выше) — иначе источник заблокируется как «мёртвый».
- git у проекта НЕТ → бэкапы `.bak-YYYY-MM-DD_метка` перед правкой.
- Ключ Gemini на месте (живой мозг работает). Пульт: `python panel/serve.py` → 127.0.0.1:8737.

## План работ (черновик)
1. Решить А/Б (где живёт логика) и набор источников (см. развилки выше) — с юзером.
2. Добавить фетчеры источников (keyless сначала), диспетчер `source`/список `sources`.
3. `harvest.py`: `_harvest_env()` → передавать список источников; `_source_signature()` → отпечаток объединения.
4. Тесты: расширить test_collect_source (каждый источник + degrade + неизвестный), test_harvest.
5. Прогнать `python cyborg/harvest.py --force`, глянуть копилку, проверить дедуп между источниками.
6. Все тесты зелёные (был 91). Обновить `.brain/changelog.md` и память.

## Ключевые файлы
- Ядро сбора: `idea_engine/organs/collect_source.py` (+ тест `idea_engine/tests/test_collect_source.py`).
- Драйвер: `cyborg/harvest.py` (SOURCE_N, _harvest_env, _source_signature) + `cyborg/tests/test_harvest.py`.
- Конвейер/проводка: `cyborg/wiring.py` (build_harvest_organs), `cyborg/orchestrator.py`.
- Копилка: `cyborg/stash.py`, данные `cyborg/data/idea_stash.md` (35 идей, все из HN).
- Пульт (если показывать источники в UI): `panel/index.html`, `panel/serve.py`.

## Запуск/проверка
- Один форс-прогон: `python M:/projects/kiborg/cyborg/harvest.py --force`
- Тесты: из `cyborg/` и `idea_engine/` — `python -m pytest` (или unittest). Базлайн 91 зелёный.
