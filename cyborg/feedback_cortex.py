"""B4 feedback_cortex — фоновый адаптер весов совета по сигналам триажа.

Читает idea_engine/data/triage_events.jsonl, обновляет cyborg/data/council_weights.json
(EMA α=0.02, старт после ≥20 событий, decay к равномерному каждые 30, мин вес 0.15).
Активирует council_weights.enabled=true только после накопления порога. НЕ блокирует
пайплайн (запуск cron'ом раз в час).

Логика адаптации (pure-функции, тестируемые без I/O):
- adapt_weights(events, current) → {enabled, weights, updated_after}. Frozen start до 20
  событий (канон, disabled). После — EMA на основе сигналов take(+)/trash(−)/later(нейтрально).
- apply_decay(weights, factor) — подтягивает к равномерному (1/3 каждому), каждые 30 событий.
- enforce_min_weight(weights, floor) — никто не выключается полностью (мин 0.15).

Сигналы по action:
- take = идея понра → советник, судивший эту идею (judged="council"/"solo"), получает +
- trash = идея отвергнута → советник получает −
- later = нейтрально (не + не −, идея отложена)

EMA α=0.02 — медленная адаптация (раз в час, нужен устойчивый тренд, не шум).

main() — cron-точка входа: load events → adapt → save council_weights. Тонкая обёртка.
"""

# Константы адаптации (тестируются как параметры pure-функций)
THRESHOLD = 20  # минимум событий для активации (frozen start до порога)
EMA_ALPHA = 0.02  # медленная адаптация (часовой cron, не шум)
DECAY_FACTOR = 0.95  # подтягивание к равномерному каждые 30 событий
DECAY_EVERY = 30  # период decay
MIN_WEIGHT = 0.15  # никто не выключается полностью
UNIFORM = 1.0 / 3  # равномерное распределение (3 советника)

ALL_ADVISORS = ["rank_ideas", "ask_llm", "orchestra"]


def adapt_weights(events, current):
    """Адаптация весов по событиям триажа.

    events = [{action, judged, ...}]. current = {name: weight} (канон mind.WEIGHTS).
    Возвращает {enabled, weights, updated_after}.

    Frozen start: < THRESHOLD событий → канон, enabled=false.
    После порога: EMA на сигналах take(+)/trash(−), decay к равномерному каждые DECAY_EVERY,
    мин вес MIN_WEIGHT. enabled=true.
    """
    n = len(events) if isinstance(events, list) else 0
    if n < THRESHOLD:
        return {"enabled": False, "weights": dict(current), "updated_after": n}

    # сигнал: take → +1 (советнику), trash → −1, later → 0. judged указывает кто судил
    # (council — все 3 советника, solo — только арбитр rank_ideas).
    # Накапливаем per-advisor сигнал, нормируем к [-1, 1].
    signal = {name: 0.0 for name in ALL_ADVISORS}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        action = ev.get("action")
        judged = ev.get("judged", "council")
        if action == "take":
            val = 1.0
        elif action == "trash":
            val = -1.0
        else:
            continue  # later нейтрально
        if judged == "solo":
            signal["rank_ideas"] += val  # solo = судил один арбитр
        else:
            for name in ALL_ADVISORS:
                signal[name] += val  # council = судили все
    # нормировка сигнала к [-1, 1] (по максимуму)
    max_abs = max((abs(v) for v in signal.values()), default=0.0) or 1.0
    norm_signal = {name: signal[name] / max_abs for name in ALL_ADVISORS}

    # EMA: new = (1−α)×old + α×(old + signal_direction×margin)
    # margin = насколько вес может сдвинуться (ограничено, чтобы EMA не прыгал)
    _MARGIN = 0.1  # макс сдвиг за один цикл адаптации (после нормировки)
    weights = {}
    for name in ALL_ADVISORS:
        old = current.get(name, UNIFORM)
        target = old + norm_signal[name] * _MARGIN
        weights[name] = (1 - EMA_ALPHA) * old + EMA_ALPHA * target

    # decay к равномерному каждые DECAY_EVERY событий
    decay_steps = n // DECAY_EVERY
    for _ in range(decay_steps):
        weights = apply_decay(weights, DECAY_FACTOR)

    weights = enforce_min_weight(weights, MIN_WEIGHT)
    return {"enabled": True, "weights": weights, "updated_after": n}


def apply_decay(weights, factor):
    """Подтянуть веса к равномерному (1/3): new = factor×old + (1−factor)×uniform."""
    out = {}
    for name in ALL_ADVISORS:
        old = weights.get(name, UNIFORM)
        out[name] = factor * old + (1 - factor) * UNIFORM
    return _renormalize(out)


def enforce_min_weight(weights, floor):
    """Никто не ниже floor, даже после перенормировки. Итеративный clamp+renormalize:
    после нормировки деление может опустить кого-то ниже floor → повторяем до стабилизации."""
    out = {name: float(weights.get(name, 0.0)) for name in ALL_ADVISORS}
    for _ in range(10):  # достаточно для сходимости (3 советника, маленькая правка)
        out = {name: max(out[name], floor) for name in ALL_ADVISORS}
        prev = dict(out)
        out = _renormalize(out)
        if all(abs(out[name] - prev[name]) < 1e-9 for name in ALL_ADVISORS):
            break
    # финальный clamp (гарантия floor после последней нормировки)
    return {name: max(out[name], floor) for name in ALL_ADVISORS}


def _renormalize(weights):
    """Нормировать веса к сумме 1 (mind._live_weights делает то же для AVG-компонента)."""
    total = sum(weights.get(name, 0.0) for name in ALL_ADVISORS)
    if total <= 0:
        return {name: UNIFORM for name in ALL_ADVISORS}
    return {name: weights.get(name, 0.0) / total for name in ALL_ADVISORS}


def main():
    """Cron-точка входа: load triage_events → adapt → save council_weights. Тонкая обёртка."""
    import os
    import sys

    # idea_engine/data/triage_events.jsonl (там же где state.json)
    ie_data = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "idea_engine", "data")
    events_path = os.path.join(ie_data, "triage_events.jsonl")
    if not os.path.exists(events_path):
        print("[feedback_cortex] нет triage_events.jsonl — нечего адаптировать")
        return
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "idea_engine"))
    try:
        import triage_events

        triage_events.PATH = events_path
        events = triage_events.load()
    except Exception as e:
        print(f"[feedback_cortex] не прочитать triage_events: {e}")
        return

    import council_weights

    current = council_weights.current_weights() if council_weights.is_enabled() else council_weights.DEFAULT_WEIGHTS
    result = adapt_weights(events, dict(current))
    council_weights.save(result)
    status = "активирован" if result["enabled"] else f"frozen (нужно {THRESHOLD} событий, есть {len(events)})"
    print(f"[feedback_cortex] {status} · веса: {result['weights']}")


if __name__ == "__main__":
    main()
