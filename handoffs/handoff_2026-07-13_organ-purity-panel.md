---
file: M:/projects/kiborg/handoffs/handoff_2026-07-13_organ-purity-panel.md
model_min: claude-sonnet-5   # хвосты (тесты) — Sonnet; глубокий рефактор органов — Opus
effort: medium
tags: [kiborg, organ-purity, panel, finish_sink, scrub_secrets, tests]
date: 2026-07-13
---

# Kiborg — чистота метафоры органов + отражение на пульте

## Что сделано этой сессией

**1. Цель (/goal): каждый орган делает только свою метафоричную функцию — ВЫПОЛНЕНО.**
- Единственный орган-модуль, лезший в чужое: `finish_sink` (Левая рука) сам чистил секреты
  (работа Печени = scrub_secrets). Убрал.
- `cyborg/finish_sink.py` — теперь ТОЛЬКО кладёт нудж (set_finish, дорожка B). Ни импорта,
  ни вызова scrub. `_scrub_nudge` удалён.
- `cyborg/wiring.py` — `_run_finish_sink` теперь ведёт нудж через Печень: новый `_liver_clean`
  (scrub_secrets.scrub_text по title/why) ДО finish_sink.run. Нервы фильтруют, рука кладёт.
- Безопасность цела: секрет вычищается ДО диска, но Печенью на уровне конвейера, не рукой.
  Проверено: в проде `finish_sink.run` зовётся ТОЛЬКО из `_run_finish_sink` (grep), обхода нет.
- Тесты `cyborg/tests/test_finish_sink.py`: `test_scrubs`→`test_pipeline_scrubs` (через
  wiring._run_finish_sink), добавлен `test_hand_alone_is_pure_placement` (рука напрямую кладёт
  как есть — доказывает чистоту).

**2. Пульт (panel/index.html) отражает правку — видно глазами:**
- Конвейер «доделать»: `finish_step → 🧼 Печень («в нервах», пунктир) → finish_sink`.
- Печень на пути nudge — НЕ звено графа (оркестратор brain.py — И по входам; scrub_secrets
  consumes=['ideas_best']). `finishChain()` вставляет её отдельным `_via`-узлом; клик = `showVia()`
  честно поясняет «чистка в обёртке руки, не узел графа» (не идейный контракт ideas_best→ideas_safe).
- Подписи PARTS выправлены: Левая рука «только кладёт», Печень «на обоих путях, никто больше не чистит».
- Ранее в сессии: три секции (Копилка/Инбокс/Журнал) сделаны сворачиваемыми (клик по заголовку,
  состояние в localStorage — `toggleSec`/`applyCollapsed`).

**3. Состязательная проверка (workflow, 4 агента по коду): 0 блокеров.** Runtime-truth / js-safety /
   blurb-accuracy — OK. panel-honesty нашёл минорный шов (клик по вставленной Печени показывал
   чужой I/O + 3 узла на схеме против 2 в журнале) — ПОЧИНЕН `_via`-узлом.

## ⚠️ Как гнать тесты (важно!)
- `python run_tests.py` рабочим питоном С pytest: **`M:/projects/darbot/venv/Scripts/python.exe`**.
- funpay-venv (основной) БЕЗ pytest → раннер ложно рапортует «ВСЕ ЗЕЛЁНЫЕ» при passed=0. Не верить.
- Итог после правок: **129 passed, 2 failed** (0 новых падений от моей работы).

## Открытые хвосты (гейты юзеру)
1. 🔴 **2 красных теста в `cyborg/tests/test_harvest.py`** (`test_status_from_out_per_source`,
   `test_harvest_env_carries_multiple_sources`) — следствие МОИХ ранних правок этой сессии:
   `harvest.SOURCES` сокращён до `["telegram"]` + добавлен флаг `beta` в `_status_from_out`
   (тесты под старую мульти-форму из 5 источников). Починить = ЛИБО вернуть 5 источников
   (раскомментить полный набор в harvest.py), ЛИБО обновить тесты под «один + бета».
   **Спросить юзера, что хочет** — это продуктовое решение, не техническое.
2. **Глубже (по желанию):** вынести seen-память с пути глаз (`_run_collect`) и курсор ног
   (`_run_finish`) в отдельные органы («Память»/«Позиция»). НО: это не нарушение цели — у этих
   функций нет органа-владельца, чтобы «красть». Отдельная стройка, только если юзер захочет.

## Состояние окружения
- Пульт (preview) ЖИВ на 127.0.0.1:8737 (serverId в preview_list, name kiborg-panel).
  Юзер просил НЕ гасить — оставлен работать.
- Telegram-источник сейчас: 1 канал `@tproger`, `SOURCES=["telegram"]` (демо-режим наблюдения).
  Полный набор (5 лент, 21 канал) закомментирован в harvest.py.
- Снапшоты перед правкой: `cyborg/*.bak-20260713_012155-organ-purity`.
- git у проекта НЕТ — правки необратимы кроме как через .bak.
- .brain/changelog.md обновлён (2 записи 2026-07-13).
