"""C4 shadow_metrics — append-only журнал «что было бы» для lazy orchestra.

A2 (lazy orchestra) НЕ включён в бою (канон = «оркестр всегда», решение юзера
2026-07-13). Но прежде чем включать lazy — надо понять, полезен ли он вообще:
как часто rank_ideas и ask_llm реально расходятся (т.е. как часто оркестр
действительно был бы нужен для разрешения)? Shadow-режим отвечает на этот вопрос
БЕЗ изменения поведения: на каждом канон-прогоне (где оркестр и так голосует)
считаем overlap топ-3 rank_ideas×ask_llm из breakdown и пишем запись в журнал.
Через неделю смотрим долю «расхождение» — если она мала, lazy бесполезен; если
велика — lazy не сэкономит почти ничего. В любом случае — данные для решения.

Формат записи (одна строка JSON):
    {"ts": <iso>, "overlap": <0..1>, "would_call_phase2": <bool>,
     "top_rank": [id...], "top_ask": [id...], "n_ideas": <int>, "n_reviewers": <int?>}

- overlap = Jaccard(top_rank, top_ask) по топ-3 множествам id.
- would_call_phase2 = overlap < 2/3 (порог A2).
- n_reviewers — сколько моделей в оркестре (для оценки цены, которую бы сэкономил lazy).

Только stdlib. PATH патчится в тестах. append-only (одна строка за запись).
Битый файл при load → graceful (пропускает неразборчивые строки). Запись
никогда не роняет прогон — shadow_metrics НИКОГДА не должен ломать конвейер
(он наблюдатель, а не участник).
"""

import datetime
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PATH = os.path.join(DATA, "shadow_metrics.jsonl")


def append(record):
    """Дописать запись в jsonl. Создаёт файл/каталог при необходимости.

    record = dict с полями overlap/would_call_phase2/top_rank/top_ask/n_ideas.
    ts проставляется здесь если нет. Ничего не возвращает — только пишет.
    """
    if not isinstance(record, dict):
        return
    if "ts" not in record:
        record = {**record, "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    parent = os.path.dirname(PATH) or "."
    os.makedirs(parent, exist_ok=True)
    with open(PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load():
    """Прочитать все записи. Битые строки пропускаются (graceful). Возвращает список dict."""
    if not os.path.exists(PATH):
        return []
    out = []
    with open(PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict):
                    out.append(rec)
            except (json.JSONDecodeError, ValueError):
                continue  # битая строка — пропускаем, не роняем аналитику
    return out
