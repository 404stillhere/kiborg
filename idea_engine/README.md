# Idea Engine — органы генерации идей и планов

Пакет органов для kiborg: сбор сырья, генерация идей, их отбор и доставка,
а также **Oracle-режим** — построение пошаговых планов по цели для локального проекта.

## Дорожки

- **Идеи (ideas):** `collect_source → ideate → rank_ideas → readability_gate → scrub_secrets → deliver`.
  Идеи копятся в `data/inbox.md`, cap=0 по умолчанию — поток не заперт.
- **Oracle:** `oracle_scan → oracle_plan → deliver_oracle`. Сканирует проект,
  строит план через LLM, сохраняет в `data/oracles/{slug}/{date}.md` и кладёт карточку в инбокс.

## Запуск

```
# идеи
python run.py tick [--seed FILE]
python run.py status <id> take|later|trash
python run.py show

# oracle
python ../cyborg/run.py --mode oracle --project "M:/projects/myapp" --goal "добавить авторизацию"
# или напрямую
python ../cyborg/oracle_mode.py --project "M:/projects/myapp" --goal "добавить авторизацию"
```

## Органы (контракт `run(inputs, env)`)

| Орган | Что делает | env |
|---|---|---|
| `organs/collect_source.py` | items из лент / локальных папок | `source`/`sources`, `n`, `timeout` |
| `organs/ideate.py` | items → идеи | `llm`, `k` |
| `organs/rank_ideas.py` | отбор идей советом | `llm`, `keep` |
| `organs/readability_gate.py` | переписывание для читаемости | `llm`/`score_llm`, `min_score` |
| `organs/scrub_secrets.py` | чистка секретов из текста | — |
| `organs/deliver.py` | идеи → `data/inbox.md` | `cap`, `inbox_path` |
| `organs/oracle_scan.py` | карта проекта: файлы, entrypoints, README | `oracle_root` |
| `organs/oracle_plan.py` | карта + цель → план | `oracle_goal`, `llm` |
| `organs/deliver_oracle.py` | план → `data/oracles/` + индекс + inbox | `inbox_path` |

> Полная цепочка органов собирается в `../cyborg/wiring*.py`. Idea Engine — библиотека органов.

## Проверено

`python ../run_tests.py` — 172 теста idea_engine + 473 cyborg + 89 panel = 734 passed.

- Живой end-to-end: наполнил 3 (llm) → полно → режим B (пул 17) → разобрал → долил (stub).
- Состязательная проверка 3 скептиками (прод-безопасность GREEN, контракт YELLOW-ок).
  Нашли RED: потолок пробивался через `set_status(id,"open")` — **исправлено**:
  переоткрытие теперь гейтит `has_room()`, `add_idea` форсит служебные поля,
  cap авторитетен из конструктора, CLI пускает только take/later/trash.

## Следующий шаг
Назвать киборгу его дом окончательно, прикрутить реальный ключ к `ideate` и ТГ-доставку,
подменить `collect`/`ideate` на извлечённые органы. Расширять — по одному.
