---
model: opus
effort: high
tags: [kiborg, cyborg, orchestrator, ebash, feature-lab, handoff]
date: 2026-07-11
---

# Handoff: киборг — ночной ebash-цикл (оркестратор доращён + пакет фабрики на разбор)

**Память:** [[kiborg-beta-orchestrator-2026-07-11]], [[kiborg-first-slice-idea-engine-2026-07-11]], [[kiborg-koncepciya-2026-07-10]]
**Проект:** `M:/projects/kiborg/` (idea_engine — первый срез; cyborg — оркестратор). Остановлено по «остановочка».

## Что случилось
Юзер: «делай бета-версию, ебашь», ушёл спать. Собрана бета оркестратора киборга (по вердикту
совета 5 моделей), затем автономный ebash-цикл её доращивал. Всё обратимо, прод НЕ тронут.

## Состояние (тесты 16/16 cyborg + 10/10 idea_engine, всё зелёное)
Цепочка киборга: `collect → ideate → scrub → deliver` (+ finish_step для «доделай»). Сделано за цикл (5 START):
1. **deliver** — доставка идей в инбокс idea_engine (cap-3 backpressure); закрыл дубль/сироту (батарея-2).
2. **лог прогонов** `cyborg/data/runs.md` (видимость).
3. **вендорен `scrub_secrets`** из реестра (копией в `organs_vendored/`) — защитный проход, секрет не утечёт.
4. **b-3 feature-lab** — 3 фичи-кандидата в `.feature-lab/` (OFF, зелёные, независимо проверены) — ЖДУТ РАЗБОРА.
5. **починка курсора finish_step** — «доделай» теперь ротирует по проектам (1vpn→DualMode→GodPC), а не залипает.

## ⛔ Открыто (гейты юзера — на утро)
1. **Разобрать 3 фичи** в `M:/projects/kiborg/.feature-lab/` (см. `README.md`):
   - feat-1 активировать мёртвый net-гейт;
   - feat-2 срез cursor-плумбинга — **УЖЕ ИЗБЫТОЧНА** (починил по корню, можно удалить);
   - feat-3 finish_sink (nudge→инбокс) — полезна.
   Нравится → вмёржить; нет → удалить `router.json` (иначе фабрика новый пакет не строит — loop-lock).
2. **Ключ к нейронке** → мозг cyborg = ask_llm вместо stub (идеи станут качественными).
3. **ТГ-бот/чат** → доставка идей в телегу (сейчас файл `inbox.md`).
4. **Автоцикл** (recurring scheduler) — если хочешь, чтобы киборг сам делал tick по расписанию.

## Честный потолок
Мозг без ключа = stub; идеи stub-качества; исполняемых органов 5 (каталог 89). Дальнейший рост
органов упирается в гейт (ask_llm разблокирует build_snapshot/check_consensus из реестра).

## Ключевые файлы
- Оркестратор: `M:/projects/kiborg/cyborg/` (core/registry/router/brain/executor/wiring/deliver/run + organs_vendored/ + tests + README)
- Первый срез: `M:/projects/kiborg/idea_engine/`
- Журнал цикла: `M:/projects/kiborg/.brain/{changelog,ebash-queue,loose-ends}.md`
- Фичи на разбор: `M:/projects/kiborg/.feature-lab/`
