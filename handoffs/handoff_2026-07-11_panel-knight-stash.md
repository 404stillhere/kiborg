---
model: claude-opus-4-8 (или sonnet для рутины)
effort: medium
tags: [kiborg, panel, конструктор, рыцарь, накопитель, harvest, loop]
date: 2026-07-11
---

# Kiborg — пульт-конструктор + облик рыцаря + накопитель идей

Проект: **M:/projects/kiborg** (единый агент из органов других проектов).
Прод idea_engine/ и cyborg/ (ядро) НЕ трогали — вся работа в panel/ и в новых файлах cyborg/.

## Что сделано в этой сессии (по порядку)

1. **Инвентаризация** (workflow 6 агентов) — полная карта что киборг умеет. Итог в
   memory `kiborg-inventory-2026-07-11.md`. Ключ Gemini юзер удалял и вернул (gemini.md на месте).

2. **ПУЛЬТ** `panel/` (serve.py + index.html, stdlib, 127.0.0.1:8737). Запуск:
   `python M:/projects/kiborg/panel/serve.py` → http://127.0.0.1:8737 (или preview `kiborg-panel`).
   Показывает: органы, конвейеры, инбокс с кнопками take/later/trash, журнал runs.md,
   каталог 89, гейты, копилку. Прогон кнопкой со стримом. 3 скептика YELLOW → починено
   (анти-CSRF, залипание кнопок, честные подписи).

3. **КОНСТРУКТОР**: органы в лотке справа, юзер сам таскает их на тело (drag&drop, магнит,
   возврат в лоток). Раскладка → `panel/layout.json` (POST /api/layout).

4. **ОБЛИК РЫЦАРЯ** (по 4 рефам юзера — крестоносец, красный ромб на груди): 3 SVG-тела
   (flat/metal/poly) в `panel/bodies.js`, serve отдаёт /bodies.js, переключатель стиля в
   панели (localStorage, дефолт metal). Красный ромб = сердце-инбокс. ⚠️ bodies.js и новые
   маршруты serve.py требуют РЕСТАРТА сервера (python не хот-релоадит); index.html/layout — на лету.

5. **НАКОПИТЕЛЬ идей** (просьба: режим для автономных прогонов, когда Claude гоняет киборга сам):
   - `cyborg/stash.py` — копилка БЕЗ потолка (data/idea_stash.{jsonl,md}), дедуп через store._sig, атомарная запись.
   - `cyborg/stash_sink.py` — орган-sink (ideas_safe→delivered), фильтр болванок в LLM-режиме.
   - `cyborg/harvest.py` — CLI: `python harvest.py [N]`. Гейт «лента изменилась?» (отпечаток HN до Gemini).
   - `wiring.build_harvest_organs()` = collect→ideate→rank→scrub→STASH (без deliver/finish).
   - Тесты: **43/43** (cyborg/tests, + test_stash.py + test_harvest.py). Скептик YELLOW → всё починено.

## Последний статус

- Стоял **/loop 10м** (cron d277728b) → гонял harvest ~2 часа. **СНЯТ на остановочке.**
- Копилка застряла на **6 идей** (все llm, болванки вычищены).

## ⚡ ГЛАВНЫЙ УРОК / следующий шаг (решение ЮЗЕРА)

Копилка почти не растёт. **Корень:** collect_source берёт всего **8 верхних заголовков HN**,
лента меняется раз в часы → те же идеи → дедуп режет. Гейт+фильтр убрали холостую трату Gemini,
но НЕ узость источника. Реальный сдвиг (сам не лез — правка сбора киборга):
- **(а)** брать глубже/шире: n=8→30, HN newstories, или другие сайты (github-trending и т.п.);
- **(б)** интервал /loop 2-3ч вместо 10мин (ровнее ляжет на ритм ленты).
Вывод: 10-мин /loop на одном узком HN-источнике = мало смысла.

## Прочие открытые гейты (из инвентаризации, не тронуты)
- Дизайн пульта докрутить (в `.brain/loose-ends.md` метка ⭐ ВЕРНУТЬСЯ): облик/цвет, оживить
  (пульс сердца), пустые слоты под будущие органы каталога, дораскрыть органы.
- Роутер глух к словоформам («доделай существующий проект» = пустой цикл; рабочая — «доделать существующие проекты»).
- git у проекта НЕТ; cyborg/README.md отстал; loop-lock .feature-lab (feat-1 удалить, feat-2 не мёржить).

## Ключевые файлы
- Пульт: `panel/serve.py`, `panel/index.html`, `panel/bodies.js`, `panel/layout.json`
- Накопитель: `cyborg/harvest.py`, `cyborg/stash.py`, `cyborg/stash_sink.py`, `cyborg/wiring.py`
- Данные: `cyborg/data/idea_stash.md` (копилка человеку), `cyborg/data/runs.md` (журнал)
- Знания: `.brain/changelog.md`, `.brain/loose-ends.md`
