---
model: sonnet (или выше — задача обзорная, для впаивания в цикл лучше opus)
effort: medium
tags: [kiborg, мозг, mind, council, advisors, keychain, взвешенное-совещание]
date: 2026-07-13
---

# Handoff — kiborg: мыслящая часть мозга (взвешенное совещание)

## Проект
`M:/projects/kiborg` — персональный агент-генератор идей. Собирается из «органов».
Git: GitHub 404stillhere/kiborg, ветка master. Тесты: `python run_tests.py` из корня
(⚠️ голый `pytest` из корня ВРЁТ — коллизия имён; только раннер).

## Что сделано этой сессией
Спроектирована и построена **мыслящая часть мозга** — площадка под 3 модуля-советника.
Ядро `brain.py`/`core.py` НЕ тронуто (заморозка). Выкачено: коммит **3ae6db8** (запушен).

**Три советника + веса важности (задал юзер):**
- rank_ideas — арбитр (0.41), живой орган киборга.
- ask_llm — интуиция (0.39), DarBench/organ.js.
- orchestra — совет (0.20), Dual Mode/organ.py (review_content).

**Иерархия `think()` (дефолт):** арбитр всегда · интуиция всегда + сама решает звать ли
совет (разброс top1-top2 < escalate_gap 0.15 → эскалация) · совет только при сомнении.
`deliberate()` — плоский режим (совместимость). Все воздержались → degraded → фолбэк stub.

## Ключевые файлы (новые, не трогая ядро)
- `cyborg/mind.py` — движок: WEIGHTS, opinion, deliberate, **think**, _tally, деградация.
- `cyborg/advisors.py` — 3 слота-адаптера (RankIdeas/AskLlm/Orchestra) + build_council.
- `cyborg/keychain.py` — ключи → цепочка интуиции + рецензенты совета.
- `cyborg/tests/test_mind.py` + `test_keychain.py` — 181 тест всего зелёный.
- `.brain/design/mind-council.md` — полный дизайн. `.brain/path.md` — хлебные крошки.

## Ключи (`llm_keys.env`, в .gitignore — боевые, НЕ коммитить)
- ИНТУИЦИЯ = closerouter, ЦЕПОЧКА: deepseek/deepseek-v4-pro → z-ai/glm-5 →
  meta/muse-spark-1.1 → openai/gpt-5.3-codex-spark (все на CLOSEROUTER_API_KEY).
- СОВЕТ = 7: sambanova, groq, gemini, mistral, openrouter, cohere, nvidia.
- cerebras ОТКЛЮЧЁН (keychain._COUNCIL_DISABLED, ключ 403), спека цела.
- Проверено живьём: интуиция 3/4 pong (glm-5 перемежающийся 502); совет 6/7, gemini 429-лимит.

## На чём остановились / следующий шаг
Мозг построен как КОД и выкачен, но **в живой цикл киборга НЕ впаян** — `brain.py` в
конвейере (`orchestrator.py`) ходит по-старому. Скептик 7 багов закрыл, тесты зелёные.

**Следующий шаг (ГЕЙТ юзера — не делать без команды):** впаять `think()` в конвейер —
например, на шаге отбора идей вместо одиночного rank_ideas звать весь совет через
`mind.think(...)` с `context` от `keychain` (build_chain → llm_chain, orchestra_context).
Развилки к обсуждению: (1) на КАКОМ шаге включить; (2) когда будить orchestra; (3) как
подать context из wiring, не правя ядро.

Мелкие хвосты: cerebras-ключ 403 (юзеру поправить); мусор `panel/index.html.bak-*033206`
(не в git, можно удалить).
