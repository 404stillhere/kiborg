"""B3 triage_events — append-only журнал действий триажа (Feedback Cortex сигнал).

Каждый triage (take/later/trash) в run.py дописывает событие в data/triage_events.jsonl.
Feedback Cortex (B4) читает это как сигнал для адаптации весов совета и профиля юзера.

Формат события (одна строка JSON на событие):
    {"ts": <iso>, "idea_id": <int>, "action": "take"|"later"|"trash",
     "title": <str>, "source_name": <str?>, "score": <float?>, "judged": <str?>,
     "breakdown_votes": {advisor_name: {"score": 0..1}}?}

breakdown_votes (опционально, с Фазы 2 Feedback Cortex 2026-07-24) — per-advisor голоса
совета за эту идею. Присутствует только если идея прошла через _rank_by_council (ставит
поле на карточке). Позволяет feedback_cortex наказывать/поощрять КОНКРЕТНОГО советника,
а не «всех сразу» по judged. Обратно совместимо: старые события без поля валидны.

Только stdlib. PATH патчится в тестах. append-only (атомарный append одной строки).
Битый файл при load → graceful (пропускает неразборчивые строки, не роняет).
"""

import datetime
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PATH = os.path.join(DATA, "triage_events.jsonl")


def append(event):
    """Дописать событие в jsonl (одна строка JSON). Создаёт файл/каталог при необходимости.

    event = dict с ключами ts/idea_id/action/title (остальное опционально). ts проставляется
    здесь если нет — чтобы вызывающий не думал о времени.
    """
    if not isinstance(event, dict):
        return
    if "ts" not in event:
        event = {**event, "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    parent = os.path.dirname(PATH) or "."
    os.makedirs(parent, exist_ok=True)
    with open(PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def load():
    """Прочитать все события. Битые строки пропускаются (graceful). Возвращает список dict."""
    if not os.path.exists(PATH):
        return []
    out = []
    with open(PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if isinstance(ev, dict):
                    out.append(ev)
            except (json.JSONDecodeError, ValueError):
                continue  # битая строка — пропускаем, не роняем потребителя
    return out
