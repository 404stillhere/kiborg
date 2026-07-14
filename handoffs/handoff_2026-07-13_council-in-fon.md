---
project: kiborg
path: M:/projects/kiborg
date: 2026-07-13
topic: совет из 7 в автономный фон + low-temp оценка читаемости
status: ЗАКРЫТО (доказано на живом автономном деплое)
model: claude-sonnet-5        # остаток мелкий и локальный; opus не нужен
effort: medium
tags: [kiborg, council, harvest, readability, autonomy, deployed]
---

# Handoff: совет в автономный фон + low-temp оценка (kiborg)

Продолжение из `handoff_2026-07-13_idea-summary-readability.md`. Всё ниже — ВЫКАЧЕНО
(GitHub 404stillhere/kiborg, master) и доказано сквозняком. Читать НЕ обязательно —
задача закрыта; файл на случай возврата к остатку.

## Что сделано (3 коммита, все запушены)
1. **`c65a243` — совет из 7 в фон.** Корень: впайка совета (llm_chain+orchestra)
   жила ТОЛЬКО в ручной кнопке (`cyborg/run.py`); автосбор (`cyborg/harvest.py`
   `_harvest_env`) её не нёс → фон судил ОДНИМ арбитром. Фикс — общий
   `harvest.wire_council(env)`, зовут ОБА пути (run.py + _harvest_env), не разойдутся.
2. **`af68cc2` — пометка совета в лог фона.** `harvest._log` писал без хвоста
   «| совет:» → в истории пульта фон выглядел как «один арбитр». Форматтер
   `_council_note` вынесен в общий `harvest.council_note`; пульт (`serve._read_runs`)
   хвост уже парсит.
3. **`c784c10` — low-temp оценка читаемости.** `readability_gate` судил балл через
   ask на temp 0.9 (генераторная) → рассуждающая модель изредка не отдавала чистый
   JSON scores → карточка проходила без правки. Фикс: `ask_llm.ask` принимает
   `temperature` (дефолт 0.9 байт-в-байт); `wiring._run_readability` даёт органу
   `score_llm` = ask(temp 0.2) для оценки; +1 повтор на остаточный шум. Переписывание
   осталось на llm (temp 0.9).

## Доказательство (end-to-end на автономном таймере, без ручного форса)
Прогон **18:21:16** (сам `serve._auto_loop`, пульт рестарт 17:15 + 60 мин):
`collect→ideate→rank→readability_gate→scrub→deliver | delivered=5 |
совет: оркестр ПРОСНУЛСЯ · голоса: ask_llm+orchestra+rank_ideas`.
Карточки id46-50: read_score 5/5 ([6,8,7,8,7]), 3 переписаны (<8), judged=council×5.
197 тестов зелёные, скан секретов чист на каждом коммите.

## Состояние среды
- Пульт: `M:/projects/funpay/venv/Scripts/python.exe M:/projects/kiborg/panel/serve.py`
  → http://127.0.0.1:8737. Автономность ON, интервал 60 мин (фон живёт В пульте —
  умрёт пульт, встанет фон).
- Тесты: `M:/projects/darbot/venv/Scripts/python.exe run_tests.py` из корня kiborg
  (голый pytest врёт — коллизия имён).
- Заморожено (НЕ трогать): `mind.py`/`advisors.py`/`keychain.py`/`llm_keys.env`
  (секреты, gitignore), `brain.py`/`core.py`.

## Открытый остаток — ЗАКРЫТ 2026-07-13 (коммиты dc65145 + 74be1ef, запушены)
**Ре-оценка переписанного в `readability_gate` — СДЕЛАНО.** После `_rewrite` новый why
сравнивается со старым В ОДНОМ вызове судьи (пара old|new) и правка берётся ТОЛЬКО если
новый строго лучше И текст реально изменился; иначе откат на старое. Пара, а не сольная
ре-оценка — потому что скептик поймал: батчевый балл и сольный судья калибрует по-разному,
сольная оценка систематически выше → гарантия «не хуже» была бы дырявой. +2 теста (откат
при не-росте, идентичный текст), 199 зелёных, живой смоук ОК (мутную улучшил и взял,
ясную не тронул). Правки: `idea_engine/organs/readability_gate.py` (run/докстринг/демо),
`idea_engine/tests/test_readability_gate.py`.

## Прочее открытое (не блокеры, отдельные задачи — НЕ про эту задачу)
- Латентный баг: `ask_llm` изредка бьёт 1 кириллический символ (транспорт node-subprocess,
  не промпт). Вынесен отдельной задачей.
- cerebras-ключ 403 (в совете отключён), closerouter 502 перемежающийся (терпим цепочкой).

Контекст-крошки: `.brain/paths/2026-07-13_idea-summary-readability.md`,
`.brain/changelog.md` (низ).
