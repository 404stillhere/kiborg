# kiborg — автономный агент «приносит идеи»

![CI](https://github.com/404stillhere/kiborg/actions/workflows/ci.yml/badge.svg)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Один агент, собранный из органов (извлечённых из других проектов), с одной работой:
**приносить свежие идеи** (новый проект / аддон / скилл), а когда их накопилось — толкать
к финишу старое. Работает сам по расписанию, судит идеи советом моделей, показывает работу
живьём в пульте. Необратимое/боевое без человека не трогает.

## Три пакета и как связаны

| Пакет | Что это | Ключевое |
|---|---|---|
| **`idea_engine/`** | движок-«первый срез» | органы (`collect_source`/`ideate`/`rank_ideas`/`readability_gate`/`finish_step`) + `store.py` (две дорожки A/B, потолок). Демо-`tick` короткий. См. `idea_engine/README.md` |
| **`cyborg/`** | платформа поверх органов | роутер + `wiring.py` (живая цепочка), `harvest.py` (автосбор в фоне), `mind.py`+`advisors.py` (взвешенный совет на отбор идей), `executor.py` (прод-гейт). См. `cyborg/README.md` |
| **`panel/`** | пульт (UI) | живой статус источников, кнопки «принеси идеи»/«наблюдать»/автономность. `serve.py` (сервер) + `index.html` + `bodies.js` (SVG-облик) |

Слои чистые: `cyborg` → `idea_engine` (в одну сторону), `idea_engine` самодостаточен.

## Живая цепочка (что делает один прогон)

```
collect_source → ideate → rank_ideas → readability_gate → scrub_secrets → deliver
   (сырьё)      (идеи)   (судья, топ)   (читаемость why)   (чистка)      (в инбокс)
```

Источники (все публичные, без ключа, кроме ТГ): Hacker News · Reddit · Lobsters · GitHub Trending ·
Telegram (через личный аккаунт) · локальные папки (`files` — файлы как сырьё, задаются в пульте,
секреты отсеиваются). Какие ленты ВКЛЮЧЕНЫ — юзер решает тумблерами в пульте (`cyborg/feeds.py`,
`data/feeds.json`); папки — отдельным блоком. Дефолт — только Telegram. Отбор идей — не одним
судьёй, а взвешенным **советом** (`mind.deliberate`: арбитр `rank_ideas` + интуиция `ask_llm` +
оркестр из нескольких моделей).

## Запуск

```bash
# Пульт (живёт на funpay-venv), http://127.0.0.1:8737
M:/projects/funpay/venv/Scripts/python.exe panel/serve.py

# Тесты — ТОЛЬКО через раннер из корня (голый pytest врёт: коллизия имён пакетов)
M:/projects/darbot/venv/Scripts/python.exe run_tests.py
```

Автономный фон живёт ВНУТРИ пульта (умрёт пульт — встанет фон). Кнопкой в пульте включается
автосбор по интервалу; каждый прогон — свежий подпроцесс `harvest.py`.

## CI и разработка

Каждый push/PR в `master` проходит GitHub Actions: 452 теста (`python run_tests.py`) +
линтер (`ruff check .`) + форматирование (`black --check .`). См. бейдж вверху.

Как вносить изменения — см. **[CONTRIBUTING.md](CONTRIBUTING.md)**:
создать ветку → локальные проверки → PR → дождаться зелёного CI → squash-merge.

## Заморожено (без явного разрешения не трогать)

- **Секреты:** `cyborg/llm_keys.env` (ключи цепочки/совета) — в `.gitignore`, значения не логируются и не коммитятся.
- **Ядро мысли:** `cyborg/mind.py`, `cyborg/advisors.py`, `cyborg/keychain.py` — движок совета (правит параллельная сессия).
- **Ядро планировщика:** `cyborg/brain.py`, `cyborg/core.py`.
- Правки самого `collect_source.py` и роутера — гейт человека (ядро сбора/маршрутизации).

## Переменные окружения

Основные env-переменные, влияющие на работу kiborg:

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `KIBORG_LLM_KEYS` | Путь к файлу с LLM ключами | `./llm_keys.env` |
| `KIBORG_SLEEP_ORCHESTRA` | Если задано — совет/судья спят (только интуиция) | не задано |
| `KIBORG_ALERT_TOKEN` | Токен Telegram-бота для алертинга (Phase 2) | не задано |
| `KIBORG_ALERT_CHAT_ID` | Chat ID для отправки алертов (Phase 2) | не задано |
| `PYTHONUNBUFFERED` | Рекомендуется `1` для корректного логирования в файлы | не задано |

### LLM ключи

Файл `llm_keys.env` содержит API ключи для провайдеров (шаблон: `deployment/llm_keys.env.example`). Ключи нужны для:

- **Интуиция** (ask_llm): `CLOSEROUTER_API_KEY` — closerouter.dev
- **Совет/судья** (advisors): `SAMBANOVA_API_KEY`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`, и др.

**Без ключей:** kibорг переходит в stub-режим (идеи генерируются детерминированными заглушками, без LLM вызовов). Полезно для тестирования.

**Заполнение ключей:**

```bash
# Скопировать шаблон
cp deployment/llm_keys.env.example llm_keys.env

# Редактировать
nano llm_keys.env  # вставить реальные ключи
```

⚠️ **ВАЖНО:** `llm_keys.env` в `.gitignore` — никогда не коммитить реальные ключи.

### Алертинг (Phase 2)

При критических сбоях (`brain_down`, `dropped_stub>0`) kibорг может отправлять алерты в Telegram. Для этого задайте:

```bash
export KIBORG_ALERT_TOKEN="123456:ABC-DEF"
export KIBORG_ALERT_CHAT_ID="987654321"
```

Без этих переменных алерты печатаются в stdout с префиксом `[ALERT]`.

## Где что искать

- Детали пакетов — их `README.md` (`cyborg/`, `idea_engine/`).
- Живая память проекта, бэклог, решения — `.brain/` (не в git).
- Ночная фабрика фич (б-3): песочница `.feature-lab/` СНЕСЕНА (2026-07-14, замок петли убран); фабрика
  строит свежий пакет с нуля, судья — человек. Не в git.
