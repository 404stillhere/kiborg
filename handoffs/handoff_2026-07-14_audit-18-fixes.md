---
model: claude-opus-4-8
effort: high
tags: [kiborg, ebash, audit, fixes, awaiting-выкатывай]
date: 2026-07-14
---

# kiborg — критаудит + 18 фиксов (ebash), ждёт «выкатывай»

**Проект:** `M:/projects/kiborg` (генератор идей: collect→ideate→rank/совет→readability→scrub→deliver). Git, ветка `master`.

## Статус: СДЕЛАНО, но НЕ закоммичено
- Критаудит через Workflow (47 агентов, 7 линз + адверсариальная верификация) → 34 находки → **18 фиксов**.
- Тесты **239 → 274 зелёных** (`python run_tests.py` — рабочий интерпретатор `C:\Program Files\Python312\python.exe`, НЕ funpay-venv: там нет pytest).
- Дифф ревизован, secret-clean (секрето-образное = только тест-фикстуры). ~23 файла изменено.
- **Полный список #1–#18 → `.brain/changelog.md`.** Аудит-бэклог + остаток → `.brain/loose-ends.md`.

## Закрыто (по корням)
- **root #1** fail-open (мусор за идеи + рапорт здоровья): stub-фильтр в deliver, честный live/fallback статус судьи, видимая деградация (⚠) в логе/пульте.
- **root #2** дырявый scrub_secrets: не ловил Gemini `AIza…`/JWT/URI-креды/вебхуки — починено +тесты.
- **root #4** дрейф доков: «потолок 3»→cap=0, «6 кандидатов→12/топ-5» по коду+README+панели.
- Гонки `state.json`: atomic write (tmp+pid+os.replace) + O_EXCL `store.state_lock` вокруг deliver/tick/status + panel-triage refuse-while-running.
- seen_items жёг посты до генерации → двухфазно (filter mark=False + mark_seen после успеха).
- double-fetch телеги за тик → переиспользование гейт-фетча (prefetched_out).
- brain-слой (всё в advisors.py, mind.py НЕ тронут): вердикт fail-closed 0.0, арбитр абстиненция при мёртвом ключе, импутация пропущенных баллов интуиции.

## СЛЕДУЮЩИЙ ШАГ — за юзером
1. **«выкатывай»** → тесты + скан секретов + коммит + пуш 18 фиксов (git-репо готов).
2. Остаток гейтнут ЮЗЕРОМ (не чинить без него): дедуп-подмножества (product, Jaccard 0.6 оставлен), панель-копилка (резервация дизайна + мёртвый backend `build_harvest_organs`/`stash_sink` — 3 опции в loose-ends), tg-сессия (резервация source + SQLite-lock не offline-проверяем), роутер-словоформы (deferred, near-zero impact).

## Урок сессии
Хук 7× выбил мою over-conservatism: гейт ТОЛЬКО по точному списку заморозки (`core.py`/`mind.py`/`keychain.py`),
явной резервации юзера, product-суждению. «Близко к замороженному / низкоценно / рискованно / invasive» — НЕ гейт.
См. память `feedback-gate-only-exact-frozen-list`.
