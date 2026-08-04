# Cyborg — оркестратор и оболочка kiborg

Собирает органы в одного агента. Конвейер идей: `collect_source → ideate → rank_ideas → readability_gate → scrub_secrets → deliver`.
Также поддерживает **Oracle-режим**: `oracle_scan → oracle_plan → deliver_oracle`.

## Архитектура

- **`router.py`** — отбирает релевантные органы по цели (без LLM).
- **`brain.py`** — планировщик: LLM при `env['llm']`, иначе детерминированный stub.
- **`executor.py`** — safe_mode: опасные/ключевые органы не запускаются без разрешения.
- **`core.py`** — `Organ` + `Memory` (env.memory, blocked-органы).
- **`registry.py`** — каталог `_shared/organs.json`.
- **`wiring*.py`** — исполняемые органы и их нервы.
- **`ask_llm.py`** — интуиция: `z.ai → native (mistral/openrouter/groq) → closerouter`.
- **`notify.py`** — Telegram-уведомления о доставленных идеях.
- **`oracle_mode.py`** — CLI для Oracle-режима.
- **`orchestrator.py`** — главный цикл `Cyborg.run(goal, env)`.

## Запуск

```bash
cd M:/projects/kiborg/cyborg

# идеи
python run.py "приноси свежие идеи"

# oracle
python run.py --mode oracle --project "M:/projects/myapp" --goal "добавить авторизацию"
# или напрямую
python oracle_mode.py --project "M:/projects/myapp" --goal "добавить авторизацию"

# тесты
python -m unittest discover -s tests -p "test_*.py"
```

## LLM-ключи

Читаются из `llm_keys.env` (в `.gitignore`). Приоритет: `ZAI_API_KEY` → `MISTRAL_API_KEY`/
`OPENROUTER_API_KEY`/`GROQ_API_KEY` → `CLOSEROUTER_API_KEY`.

## Уведомления

Telegram: задать `KIBORG_NOTIFY_TOKEN` и `KIBORG_NOTIFY_CHAT_ID`. При доставке идей
придёт краткая сводка.

## Проверка

`python ../run_tests.py` — 480 тестов cyborg + 172 idea_engine + 89 panel = 741 passed.
