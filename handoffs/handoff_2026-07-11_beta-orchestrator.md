---
model: opus
effort: high
tags: [kiborg, orchestrator, beta, organs, council, verification]
date: 2026-07-11
---

# Handoff: бета оболочки-оркестратора киборга собрана

**Память:** [[kiborg-beta-orchestrator-2026-07-11]], [[kiborg-first-slice-idea-engine-2026-07-11]], [[kiborg-koncepciya-2026-07-10]]
**Проект:** `M:/projects/kiborg/cyborg/`

## Что случилось (ночь, юзер спал)
Юзер передал полное управление («делай бета-версию, ебашь»). Собрана **бета оркестратора** —
первая штука, что реально СОБИРАЕТ органы в одного агента. Основа — вердикт совета 5 моделей
(прогон council-broadcast 2026-07-11): после сбора органов дальше не 48-й орган, а оркестратор.

## Устройство (stdlib, без venv)
Агентный цикл: `цель → РОУТЕР (отбор подмножества) → МОЗГ (stub/LLM) → ИСПОЛНИТЕЛЬ (прод-гейт) → ПАМЯТЬ → повтор → результат`.
- `router.py` — не отдавать мозгу все органы разом (ключевой инсайт совета).
- `brain.py` — stub-планировщик (без ключа) / LLM (`env['llm']`=ask_llm в проде).
- `executor.py` — прод-гейт (needs.prod в safe_mode не запускается), ошибки не роняют цикл.
- `core.py` — Organ + Memory (produced/blocked). `orchestrator.py` — Cyborg.run.
- `wiring.py` — 3 исполняемых органа из idea_engine. `registry.py` — каталог `_shared/organs.json` (89).
- CLI: `python run.py "приноси свежие идеи"` / `"доделай существующий проект"`.

## Проверено
- `python -m unittest discover -s tests -p "test_*.py"` → **10/10**.
- Живьём: «приноси идеи» → роутер [collect_source, ideate] → идеи из HN. «доделай» → finish_step → «доделать 1vpn» из recon.
- **3 скептика (ultracode):** прод-безопасность **GREEN**, честность **GREEN**, логика **RED**
  (холостой спин на пустом выходе органа — collect_source при сбое HN бил 8 запросов). **ПОЧИНЕНО**
  (Memory.produced развёл «ключ записан»/«непусто»; blocked при отсутствии прогресса; llm-guard; роутер 0-match).
  Независимый скептик подтвердил **FIXED** (пустой источник = 1 вызов). Контрпример в тестах.

## Честный потолок
Мозг без ключа = stub (не LLM); идеи ideate без ключа = stub-качества; исполняемых органов 3 (каталог 89).
Осознанные пределы (README): «produced навсегда» (нет ретрая с бэкоффом); роутер 0-match отдаёт всё
(при росте набора вернуть иерархию). Прод НЕ тронут — только новые файлы под `kiborg/cyborg/`.

## Следующий шаг
1. **От юзера (доступ):** реальный ключ к нейронке → мозг = ask_llm вместо stub; ТГ-бот/чат для доставки.
2. **Моё (код):** переносить исполняемые органы из реестра группами; иерархия роутера; автоцикл.

## Ключевые файлы
- Бета: `M:/projects/kiborg/cyborg/` (core/registry/router/brain/executor/wiring/orchestrator/run + tests + README)
- Первый срез (органы, что подключены): `M:/projects/kiborg/idea_engine/`
- Скрипт проверки: `.../workflows/scripts/verify-cyborg-beta-wf_33616d0d-5e5.js`
