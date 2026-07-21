# Контракт органов kiborg

Source of truth: `cyborg/wiring_builder.py:build_organs()`. Этот документ — снимок на
2026-07-21. При изменении реестра органов обновлять сводную таблицу здесь (контракты тел —
в `idea_engine/organs/*.py`, frozen; `cyborg/{deliver,finish_sink}.py`; `cyborg/organs_vendored/`).

## Универсальный контракт тела

```python
def run(inputs: dict, env: dict) -> dict:
    ...
```

- `inputs` — словарь данных от предыдущего органа в цепочке (по `produces`/`consumes`).
- `env` — конфигурация прогона (долгоживущая, та же на всех органах): `n`, `source`, `sources`,
  `llm`/`content_llm` (callable для генерации/судьи), `direction`, `rejected`, `on_progress`,
  креды telegram/files, `llm_chain`/`orchestra` (для совета).
- **Орган НЕ трогает ключи/сеть напрямую** — только через `env["llm"]`/`env["content_llm"]` и
  креды в env. Это явный инвариант (см. docstring каждого frozen органа).
- Возвращает `dict` — результат становится новым `inputs` для следующего органа (orchestrator
  сливает их через `Memory`).

## Нервы vs тела

`build_organs()` регистрирует **обёртки-нервы** `_run_*` из `cyborg/wiring_*.py`. Нервы:
- адаптируют env под орган (подставляют дефолты, прокидывают креды);
- добавляют cross-cutting (замок tg-сессии, чистка секретов в заголовках, фильтр seen_items);
- кладут дополнительные поля в `out` (`provider`, `council`).

Реальные контракты — в **телах** (`idea_engine/organs/*.py` — frozen, `cyborg/{deliver,finish_sink}.py`,
`cyborg/organs_vendored/scrub_secrets.py`). Ниже документирован **гибридный контракт** — что
видно downstream после обёртки (то, что уходит в `out` и пишется в `runs.md`).

## Сводная таблица (8 органов)

| # | name | role | consumes → produces | body | frozen? |
|---|------|------|--------------------|------|---------|
| 1 | `collect_source` | source | `[]` → `items` | `idea_engine/organs/collect_source.py` | **frozen** |
| 2 | `ideate` | transform | `items` → `ideas` | `idea_engine/organs/ideate.py` | **frozen** |
| 3 | `rank_ideas` | transform | `ideas` → `ideas_best` | `idea_engine/organs/rank_ideas.py` | **frozen** |
| 4 | `readability_gate` | transform | `ideas_best` → `ideas_polished` | `idea_engine/organs/readability_gate.py` | **frozen** |
| 5 | `finish_step` | source | `[]` → `nudge` | `idea_engine/organs/finish_step.py` | **frozen** |
| 6 | `scrub_secrets` | transform | `ideas_polished` → `ideas_safe` | `cyborg/organs_vendored/scrub_secrets.py` | vendored (read-only) |
| 7 | `deliver` | sink | `ideas_safe` → `delivered` | `cyborg/deliver.py` | cyborg (не frozen) |
| 8 | `finish_sink` | sink | `nudge` → `delivered` | `cyborg/finish_sink.py` | cyborg (не frozen) |

`needs` (требования к safe_mode/executor): `collect_source` — `{"network": True}`; LLM-органы
(`ideate`/`rank_ideas`/`readability_gate`) — `{"key": "LLM_KEY", "stub_ok": True}`; остальные — `{}`.

## Дорожки (видно в runs.md по цепочке)

- **A «приноси идеи»**: `collect_source → ideate → rank_ideas → readability_gate → scrub_secrets → deliver`
- **B «доделай»**: `finish_step → finish_sink`

Только `deliver` и `finish_sink` имеют side-effect — пишут в `idea_engine/data/state.json` и
`inbox.md` под межпроцессным замком `state_lock` (frozen `idea_engine/store.py`, best-effort).
Остальные — чистые трансформации `inputs → out`.

---

## Контракты по полям

### 1. `collect_source` — ГЛАЗА (источник сырья)

**Обёртка:** `cyborg/wiring_collect.py:_run_collect` (+ `_collect_locked` — замок tg-сессии).

**inputs:** игнорируется (орган-источник). Нерв переиспользует `env["prefetched_out"]`, если
гейт-проверка уже сходила в источник этим тиком (не тянем телегу дважды за тик ~90с).

**env (читаемые):**
- `n` (default 8) — сколько заголовков тянуть СУММАРНО (бюджет делится между источниками).
- `source` (default `"hn"`) — единственный источник (если `sources` не задан).
- `sources` (list[str]) — активные источники. **Пустой список = нет источников** (не дефолт hn).
- `timeout` (default 8) — таймаут фетча на источник.
- keyed/конфиг-источники: `telegram_channels`, `telegram_api_id`, `telegram_api_hash`,
  `telegram_session`, `telegram_python`, `telegram_timeout`, `files_paths`.

**outputs:**
```python
{
  "items": [{"title": str, "url": str, "id": str, "source": str}, ...],
  "source": str,           # label источника (для/logs)
  "degraded": bool,        # True если все упали / фолбэк на хардкод-заголовки
  "degraded_reason": str,  # опц. — почему деградировал
  "partial_errors": [str], # опц. — упавшие источники ("name: error")
}
```

**Побочный эффект нервом:** названия items чистятся через `scrub_secrets.scrub_text` ДО генерации
(защита от утечки секрета в промпт: файл-источник может принести секрет в заголовке).

---

### 2. `ideate` — МОЗГ-генератор

**Обёртка:** `cyborg/wiring_ideate.py:_run_ideate`.

**inputs:** `{"items": [...]}`.

**env:**
- `k` (нерв ставит **12**) — сколько идей-кандидатов генерить.
- `llm` / `content_llm` (callable) — генератор. Нет ключа → stub-болванки (`brain: "stub"`).
- `direction` (опц.) — руль темы, прокидывается в генератор.
- `filter_seen_items` (опц., bool) — если True, нерв фильтрует items через `seen_items.filter_fresh`
  («уже видели») и метит виденными после успешной генерации.
- `on_progress` (опц.) — колбэк суб-прогресса.

**outputs:**
```python
{
  "ideas": [
    {"title": str, "why": str, "effort": "легко"|"средне"|"трудно", "brain": "llm"|"stub"},
    ...
  ],
  "provider": str,  # опц. (нерв добавляет при живом llm): muse-spark|deepseek|nemotron — какое
                    # плечо цепочки closerouter ответило. Пишется в runs.md: «модель=…».
}
```

**Побочный эффект нервом:** `seen_items.mark_seen` после успешной генерации (только если
`filter_seen_items=True`). При stub-fallback НЕ метит (чтобы посты не сгорели зря при транзиентном сбое).

---

### 3. `rank_ideas` — СОВЕТ (отбор топ-K идей)

**Обёртка:** `cyborg/wiring_council.py:_run_rank` (живой цикл → `_rank_by_council`, фолбэк → тело).

**inputs:** `{"ideas": [...]}`.

**env:**
- `keep` (нерв ставит **5**) — сколько оставить.
- `llm` / `content_llm` — судья-фолбэк (плоский отбор).
- `direction` (опц.).
- `llm_chain` (опц.) — цепочка интуиции (ask_llm) для совета.
- `orchestra` (опц.) — конфиг 7-модельного оркестра (`{models: [...], max_workers, timeout_sec}`).
- `council` — если `False`, совет отключается (только плоский судья).

**outputs (живой цикл, через совет):**
```python
{
  "ideas_best": [
    {...idea, "judged": "solo"|"council", "score": float},  # score 0-10 = балл совета ×10
    ...
  ],
  "council": {           # опц. — метаданные совета для runs.md («совет: оркестр ПРОСНУЛСЯ…»)
    "live": [str],       # кто голосовал: "ask_llm", "orchestra", "rank_ideas"
    "solo": bool,        # по факту судил один арбитр
    "woken": bool,       # оркестр голосовал
    "why": str,          # объяснение
  },
}
```

**outputs (фолбэк / нет ключей):**
```python
{
  "ideas_best": [{...idea, "judged": "llm"|"fallback"|"fill"}, ...],  # без council/score
}
```

---

### 4. `readability_gate` — РЕДАКТОР читаемости

**Обёртка:** `cyborg/wiring_council.py:_run_readability`.

**inputs:** `{"ideas_best": [...]}` (fallback на `ideas`).

**env:**
- `min_score` (нерв ставит **8**) — порог: карточки с баллом < 8 переписываются самонесущим текстом.
- `llm` (callable) — для переписывания (temp 0.9 — живость).
- `score_llm` (опц., callable) — отдельный детерминированный судья балла (нерв строит с
  `temperature=0.2`, чтобы JSON scores всегда парсился). Только если `llm is ask_llm.ask`.
- `on_progress` (опц.).

**outputs:**
```python
{
  "ideas_polished": [
    {
      ...idea,
      "read_score": float,    # опц. — 0-10 (если был score_llm)
      "read_fixed": bool,     # опц. — True если why переписан
      "why": str,             # обновлённый текст (если переписан), иначе исходный
    },
    ...
  ],
}
```

ВСЕГДА возвращает `ideas_polished` — даже без llm (passthrough), иначе конвейер встанет.
Идею НЕ теряем, карточку НЕ выкидываем — правим только текст `why`.

---

### 5. `finish_step` — НОГИ (режим «доделай»)

**Обёртка:** `cyborg/wiring_finish.py:_run_finish`.

**inputs:** игнорируется.

**env:**
- `recon_path` (нерв ставит `config.RECON_FILE`) — путь к backlog проектов (`recon.json`).
- `cursor` (нерв читает из `config.CURSOR_FILE`) — курсор ротации проектов.
- `skip_folders` (нерв ставит `config.SKIP_FOLDERS`) — folder'ы, которые пропускать.

**outputs:**
```python
{
  "nudge": {
    "title": str, "why": str, "effort": str,
    "kind": "finish",
    "folder": str,    # путь к проекту
    "state": str,     # статус проекта из recon
  } | None,           # None если пул пуст / курсор упёрся
  "next_cursor": int, # курсор для следующего прогона
  "pool": int,        # опц. — сколько проектов в пуле
  "error": str,       # опц. — нет файла recon и т.п.
}
```

**Побочный эффект нервом:** персистит `next_cursor` в `config.CURSOR_FILE` (cursor.json) —
ротация между прогонами.

---

### 6. `scrub_secrets` — ПЕЧЕНЬ (вычистка секретов)

**Обёртка:** `cyborg/wiring_scrub.py:_run_scrub`. Тело `cyborg/organs_vendored/scrub_secrets.py`
имеет **другой** контракт (`run({text}) → {text, redacted}`) и в organs-цепочке напрямую НЕ
зовётся — используется только `scrub_text()`.

**inputs:** `{"ideas_polished": [...]}` (fallback на `ideas_best`, затем `ideas`).

**outputs:**
```python
{
  "ideas_safe": [{...idea, "title": str, "why": str}, ...],  # поля вычищены: AQ.-ключи, sk-*, ghp-* и т.д. → [REDACTED]
  "redacted": int,  # сколько полей реально изменено (>0 = секрет просочился — безопасность!)
}
```

Чистит поля `title` и `why` каждой идеи. Нервы `wiring_collect` и `wiring_scrub` тоже зовут
`scrub_secrets.scrub_text` (защита на входе в конвейер + перед доставкой).

---

### 7. `deliver` — РУКА (доставка в инбокс)

**Обёртка:** `cyborg/wiring_scrub.py:_run_deliver` (тонкая, почти passthrough). Тело
`cyborg/deliver.py`.

**inputs:** `{"ideas_safe": [...]}` (fallback на `ideas`).

**env:** `content_llm`/`llm` — определяют `llm_mode`. В llm_mode stub-болванки (`brain: "stub"`)
отбрасываются (незачем захламлять инбокс мусором).

**outputs:**
```python
{
  "delivered": int,         # сколько идей реально легло в inbox.md
  "inbox": str,             # путь к idea_engine/data/inbox.md
  "dropped_stub": int,      # сколько stub-болванок отброшено (llm_mode)
  "dropped_dup": int,       # сколько дубликатов по дедупу
  "brain_down": bool,       # True = при живом ключе ВСЕ идеи оказались болванками (LLM не ответил)
}
```

**Побочный эффект:** пишет в `idea_engine/data/state.json` (через `Store.add_ideas`) и в
`inbox.md` под межпроцессным замком `state_lock`. Дорожка A (lane `ideas`).

**Триггер алерта:** `brain_down=True` → `alerts.maybe_alert("CRITICAL", "мозг недоступен…")`
(см. `cyborg/alerts.py`). `dropped_stub > 0` → `WARN`.

---

### 8. `finish_sink` — РУКА для «доделай»

**Обёртка:** `cyborg/wiring_scrub.py:_run_finish_sink` (сначала чистит nudge через `_liver_clean`,
потом рука). Тело `cyborg/finish_sink.py`.

**inputs:** `{"nudge": {...}}`.

**outputs:**
```python
{
  "delivered": 0 | 1,    # 1 если nudge лёг в инбокс, 0 если nudge пустой/None (no-op)
  "inbox": str | None,   # путь к inbox.md если доставка была
  "lane": "B",           # отдельный слот store.set_finish (не смешивается с идеями)
}
```

**Побочный эффект:** пишет в `state.json` (`Store.set_finish`) и `inbox.md` под `state_lock`.
Дорожка B.

---

## Как добавить свой орган

1. **Реализуй тело** — `run(inputs, env) -> dict`. Не трогай ключи/сеть напрямую, только через env.
2. **(Опц.) Обёртка-нерв** — если надо адаптировать env или добавить side-effect, создай
   `cyborg/wiring_<name>.py` с `_run_<name>(inputs, env)`. Если нет — передавай тело напрямую.
3. **Зарегистрируй в `build_organs()`** (`cyborg/wiring_builder.py`) — `Organ(name=..., purpose=...,
   run=_run_<name>, role="source"|"transform"|"sink", produces=[...], consumes=[...], tags=[...],
   needs={...})`.
4. **Проконтролируй маршрутизацию** — роутер (`cyborg/router.py`) отбирает органы по пересечению
   токенов цели с `tags`. Цель должна матчить с `tags` нового органа, иначе он не попадёт в цепочку.
5. **Обнови этот документ** — добавь строку в сводную таблицу + секцию контракта.

Органы пишутся «по одному» (см. docstring `cyborg/wiring.py`): реестр `_shared/organs.json`
содержит 89 карточек-кандидатов, но реально исполняется 8. Расти группами, не подключай все сразу.
