---
model: opus
effort: high
tags: [kiborg, cyborg, idea_engine, rank_ideas, dedup, gemini, feature-lab, ebash, handoff]
date: 2026-07-11
---

# Handoff: киборг — судья идей + память предложенного (ебаш-прогон 2)

**Память:** [[kiborg-beta-orchestrator-2026-07-11]], [[kiborg-first-slice-idea-engine-2026-07-11]], [[kiborg-koncepciya-2026-07-10]]
**Проект:** `M:/projects/kiborg/` (git НЕТ → правки через `.bak`). Остановлено по «остановочка».

## Что сделано (всё обратимо, прод не тронут; тесты 32 idea_engine + 30 cyborg зелёные)
По находкам юзера на github (auto-improve / Agent_Memory_Techniques):
1. **Судья идей** `idea_engine/organs/rank_ideas.py` — ideate генерит 6 → судья (рубрика via Gemini)
   оставляет топ-3. Конвейер: `collect→ideate(6)→rank_ideas→scrub→deliver`.
2. **Память предложенного** — дедуп в `idea_engine/store.py` (`seen`, значимые слова, Jaccard≥0.6,
   потолок 500, backfill). Не предложит похожее дважды, помнит после разбора.
3. Скептик нашёл 2 «тихой потери идей» → починены (судья добор до keep; стоп-слова в дедупе).
4. b-2: вынесен дубль `wiring._content_llm`.
5. **collect_source фикс**: `error`→`degraded_reason` при обрыве сети (киборг больше не блокирует
   источник, отдавший резерв). ⇒ feat-1 фабрики superseded.

## ⛔ На разбор ЮЗЕРА (гейты)
1. **2 фичи в `M:/projects/kiborg/.feature-lab/`** (b-3 фабрика, OFF, зелёные):
   - **feat-2 — НЕ мёржить** (схлопывает дорожку B → ломает решение finish_sink этой сессии);
   - **feat-1 — удалить** (superseded фиксом collect_source);
   - needs_manual (skip_folders) — посмотреть.
   Loop-lock: пока не пометишь reviewed / не удалишь `router.json` — фабрика новый пакет не строит.
2. **Поведение при лежащей сети** (таст-решение): киборг генерит идеи из 4 захардкоженных
   фолбэк-заголовков collect_source, выдаёт за свежие HN (дедуп отдаст 1 раз) — глушить / метить / оставить.
3. **ТГ-доставка** — нужен токен бота (сейчас идеи в файл `inbox.md`).
4. **Автоцикл** — recurring scheduler (жёсткий гейт, ebash сам cron не создаёт).

## Ключевые файлы
- Судья: `idea_engine/organs/rank_ideas.py` | Дедуп: `idea_engine/store.py` (seen/_is_dup)
- Живая модель: `cyborg/ask_llm.py` (Gemini, ключ из `gemini.md` — plaintext, не в git)
- Обвязка: `cyborg/wiring.py` | Журнал цикла: `.brain/{changelog,ebash-queue,loose-ends}.md`
- Фичи на разбор: `.feature-lab/` (router.json + README + feat-1/feat-2)
