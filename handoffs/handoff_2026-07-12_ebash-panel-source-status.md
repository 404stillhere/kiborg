---
model: claude-opus-4-8 (или sonnet для рутины/доков)
effort: medium
tags: [kiborg, ebash, panel, source-status, sources, run_tests, docs, каркас]
date: 2026-07-12
---

# Kiborg — сессия «Ебашь» (вечер 2026-07-12): пульт+источники+тулинг+доки

Проект: **M:/projects/kiborg**. Запущено словом «Ебашь». Цикл СНЯТ юзером на остановочке
(будильник погашен, cron/таски пусты). **Ядро cyborg/idea_engine НЕ тронуто** (заморозка держится),
кроме санкционированного ранее collect_source (источники). Вся работа — в panel/, harvest.py (accumulator),
доках, тестах, тулинге.

## Сделано (5 START-по-делу, все проверены вживую)
1. **Каркас-рыцарь растянут до краёв** (panel/index.html): корень — `#skel` схлопывался до 300px из-за
   `margin:0 auto` на грид-элементе (авто-поля отменяют stretch); убрал → 680px, viewBox обрезан под
   габариты рыцаря (83 28 569 509). Пруф замерами (300→680, ничего не срезано).
2. **Синхрон пульта с 5 источниками** (panel/index.html): работа по источникам шла в ДРУГОМ чате мимо
   panel/ → пульт врал (ghost collect_tg_news «не в деле» — а он живой; глаза «только HN»). Переписал
   глаза на 5 лент (HN·Reddit·Lobsters·GitHub·Telegram), убрал ghost-ТГ, websearch «третий→ещё один вход».
3. **⭐ ЖИВОЙ per-source статус в пульте** (harvest.py + serve.py + panel): панель показывает строку
   «источники (проверка HH:MM): 🟢 HN 6 · 🔴 Reddit · 🟢 Lobsters 6 · 🟢 GitHub 6 · 🟢 Telegram 4».
   harvest._source_signature (дешёвый гейт, уже фетчит объединение) вернул 4-й элемент — per-source статус;
   main() пишет data/source_status.json атомарно на КАЖДОМ авто-прогоне; serve._read_source_status →
   /api/state.sources; panel renderSources() рисует чипы, ошибка в тултипе. Reddit🔴 из ЖИВОГО
   partial_errors (403 IP-блок), НЕ хардкод. Поймал+пофиксил свой баг: проба звала collect_source без
   telegram-кредов → telegram ложно «упал»; фикс — проба берёт полный _harvest_env.
4. **run_tests.py** (корень проекта): голый `pytest` из корня даёт ЛОЖНЫЕ провалы — cyborg/ и idea_engine/
   имеют одноимённые run.py/store.py, единый прогон кэширует первый в sys.modules. Раннер гоняет пакеты
   РАЗДЕЛЬНЫМИ процессами → честный агрегат, exit-код для CI. **Так и надо гонять все тесты: `python run_tests.py`.**
5. **Доки под 5 источников** (cyborg/README.md + idea_engine/README.md): оба README отставали. cyborg —
   добавлен блок про 5 лент collect_source + partial_errors + живой статус в пульте. idea_engine — стале
   «test_store 10/10» → 17/17 (проверил прогоном) + NB, что орган вырос до 5 лент (демо пинит один hn).

## Состояние
- **Тесты: 130 зелёные** (cyborg 74 · idea_engine 45 · panel 11) — гнать через `python run_tests.py`.
- Батареи: b-1=0 (маркеры 0), b-2=0 (jscpd — дублей/сирот нет), b-3 ЗАЛОЧЕНА (feat-1/2 reviewed=false).
- Копилка: 50 идей (все llm). Источники живьём: 4/5 работают, Reddit заблокирован по IP (403) с этого хоста.

## Открытые ГЕЙТЫ на юзера (не трогал — личное/со-создание/ядро/решение)
- **Облик рыцаря** (цвет/настроение/глаза-камеры) + общая ВЁРСТКА пульта — ЛИЧНОЕ, со-создаётся, не доносить готовым.
- **Разбор копилки (50 идей)** + ранжирование «лучшее сверху» — co-create, НЕ строить самому.
- **b-3 фабрика**: разобрать feat-1 (net-гейт, суперседнута — можно удалить) / feat-2 (НЕ мёржить) — снять loop-lock.
- **Роутер словоформы** — правка ядра (заморозка): «доделай существующий проект» даёт score=0; рабочая — «доделать существующие проекты».
- **ТГ-доставка** идей (токен бота); **cron-автоцикл** (жёсткий гейт); **LLM-мозг** (ask_llm, future).
- **Другие источники** (Product Hunt=токен; ещё keyless) — юзер хотел выбирать ВМЕСТЕ (см. handoff_2026-07-12_add-idea-sources.md); не делать автономно.
- Минор: force-кнопка харвеста статус источников не обновляет (force пропускает гейт-пробу) — строка = последняя авто-проверка.

## Ключевые файлы
- Пульт: `panel/index.html` (весь UI, читается на лету), `panel/serve.py` (РЕСТАРТ при правке серверной части),
  `panel/bodies.js`, `panel/tests/test_serve.py`.
- Накопитель: `cyborg/harvest.py` (SOURCE_N=30, SOURCES=[5], _source_signature→status, _status_from_out),
  `cyborg/stash.py`, данные `cyborg/data/{idea_stash.md, source_status.json, runs.md}`.
- Сбор: `idea_engine/organs/collect_source.py` (5 источников, _SOURCES).
- Тулинг: `run_tests.py` (корень). Доки: `cyborg/README.md`, `idea_engine/README.md`.
- Журнал цикла: `.brain/{changelog.md, ebash-queue.md, loose-ends.md}`.

## NB для следующего чата
- Запуск пульта: `python M:/projects/kiborg/panel/serve.py` → 127.0.0.1:8737 (или preview kiborg-panel).
- serve.py/bodies.js требуют РЕСТАРТА сервера (python не хот-релоадит); index.html/layout — на лету.
- Все тесты: `python M:/projects/kiborg/run_tests.py` (НЕ голый pytest из корня — соврёт из-за коллизии имён).
- git у проекта НЕТ → откат через .bak с датой-временем (за сессию: index.html/serve.py/harvest.py/README × .bak-2026-07-12_*).
- Ключ Gemini на месте; живой контур (идеи+судья) работает.
