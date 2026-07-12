---
model: opus
effort: high
tags: [kiborg, idea-engine, first-slice, organs, verification]
date: 2026-07-11
---

# Handoff: первый рабочий срез киборга — idea_engine

**Память:** [[kiborg-first-slice-idea-engine-2026-07-11]], [[kiborg-koncepciya-2026-07-10]], [[organs-registry-verified-2026-07-10]]
**Проект:** `M:/projects/kiborg/idea_engine/`

## Что случилось
Гейт №1 закрыт: главная работа киборга = **«приносит идеи»**. Собран и проверен первый
тонкий срез — первая проверка, что органы работают ВМЕСТЕ (не по одному).

## Устройство (только stdlib, без venv)
- `store.py` — ядро, 2 дорожки: **A** (новые идеи, потолок 3, обратная тяга — новая не
  придёт пока не разгребёшь take/later/trash), **B** (1 слот «доделать существующее»,
  кормится из `M:/projects/panelofprojects/recon.json`, пул 17, ротация по cursor).
- `organs/collect_source.py` — тянет свежее (HN, публичный, edge/IO-орган).
- `organs/ideate.py` — items → 3 идеи с ценником; мозг через `env["llm"]`=ask_llm, иначе stub.
- `organs/finish_step.py` — режим B из recon.
- `run.py` — оболочка-драйвер: tick = A если место есть, иначе B. Пишет `data/inbox.md` + `data/notify.md`.
  CLI: `python run.py tick [--seed FILE]` / `status <id> take|later|trash` / `show`.

## Проверено
- `python -m unittest tests.test_store` → **10/10**.
- Живой end-to-end: наполнил 3 реальные идеи из свежего HN (llm-путь через --seed) →
  очередь полна → режим B («доделать 1vpn») → разобрал → долил (stub).
- 3 скептика (ultracode): прод-безопасность **GREEN**, контракт **YELLOW-ок**,
  логика **RED** — потолок пробивался через `set_status(id,"open")`. **ПОЧИНЕНО**
  (переоткрытие гейтит has_room, add_idea форсит служебные поля, CLI только take/later/trash),
  контрпример закодирован в тест.

## Честный потолок
- collect/ideate — локальные формы, НЕ извлечённые органы из `_shared/organs.json`.
- seed-мозг игнорирует prompt (стенд-ин ask_llm до ключа).
- доставка — файл, не ТГ; цикл ручной (планировщик не ставил осознанно).

## Следующий шаг (по приоритету)
1. **От юзера (доступ):** рабочий ключ к нейронке для `ideate` + ТГ-бот/чат для доставки.
2. **Моё (код):** подменить collect/ideate на реальные органы реестра; поставить автоцикл; расширять по одному.

## Ключевые файлы
- Срез: `M:/projects/kiborg/idea_engine/` (store.py, run.py, organs/, tests/, README.md)
- Инбокс-демо: `M:/projects/kiborg/idea_engine/data/inbox.md`
- Скрипт проверки: `.../workflows/scripts/verify-idea-engine-wf_4b1e969d-564.js`
