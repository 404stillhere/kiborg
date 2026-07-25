"""B4 feedback_cortex — фоновый адаптер весов совета по сигналам триажа.

Читает idea_engine/data/triage_events.jsonl, обновляет cyborg/data/council_weights.json
(EMA α=0.02, старт после ≥20 событий, decay к равномерному каждые 30, мин вес 0.15).
Активирует council_weights.enabled=true только после накопления порога. НЕ блокирует
пайплайн (запуск cron'ом раз в час).

Логика адаптации (pure-функции, тестируемые без I/O):
- adapt_weights(new_events, current, previous_count, enabled) → {enabled, weights,
  updated_after}. Frozen start до 20 событий (канон, disabled). После — каждый новый
  сигнал применяется ровно один раз; updated_after хранит курсор append-only журнала.
- apply_decay(weights, factor) — подтягивает к равномерному при каждом новом
  пересечении границы 30 событий.
- enforce_min_weight(weights, floor) — никто не выключается полностью (мин 0.15).

Сигналы по action:
- take = идея понра → советник, судивший эту идею, получает +
- trash = идея отвергнута → советник получает −
- later = нейтрально (не + не −, идея отложена)

Per-advisor (Фаза 3, 2026-07-24): если у события есть breakdown_votes (per-idea голоса
советников из Фазы 1-2), сигнал считается ПО КАЖДОМУ советнику через его голос:
    signal[name] += action_sign × (advisor_score − 0.5) × 2
Советник, что высоко оценил МУСОР → штраф; низко оценил мусор → награда (был прав).
Симметрично для take. Без breakdown_votes — fallback на грубую эвристику по judged.

EMA α=0.02 — медленная адаптация (раз в час, нужен устойчивый тренд, не шум).

main() — cron-точка входа: load events → взять хвост после updated_after → adapt → save.
"""

import math

# Константы адаптации (тестируются как параметры pure-функций)
THRESHOLD = 20  # минимум событий для активации (frozen start до порога)
EMA_ALPHA = 0.02  # медленная адаптация (часовой cron, не шум)
DECAY_FACTOR = 0.95  # подтягивание к равномерному каждые 30 событий
DECAY_EVERY = 30  # период decay
MIN_WEIGHT = 0.15  # никто не выключается полностью
UNIFORM = 1.0 / 3  # равномерное распределение (3 советника)

ALL_ADVISORS = ["rank_ideas", "ask_llm", "orchestra"]
TRIAGE_ACTIONS = {"take", "later", "trash"}


def _is_triage_event(event):
    """Есть ли в JSON-строке реальное действие пользователя, а не служебный dict."""
    return isinstance(event, dict) and event.get("action") in TRIAGE_ACTIONS


def adapt_weights(events, current, previous_count=0, enabled=False):
    """Адаптация весов по событиям триажа.

    events = НОВЫЕ [{action, judged, ...}] после previous_count.
    current = {name: weight} (канон mind.WEIGHTS или уже адаптированные веса).
    Возвращает {enabled, weights, updated_after}.

    Frozen start: суммарно < THRESHOLD реальных triage-действий → канон, enabled=false,
    курсор остаётся 0 (чтобы накопленные сигналы обработались после достижения порога).
    После порога: каждый новый batch применяется РОВНО один раз. Пустой batch = no-op.
    Decay срабатывает только при новом пересечении границы DECAY_EVERY.
    """
    batch = events if isinstance(events, list) else []
    try:
        previous_count = max(0, int(previous_count))
    except (TypeError, ValueError, OverflowError):
        previous_count = 0
    n_new = len(batch)
    n = previous_count + n_new
    valid_total = sum(1 for event in batch if _is_triage_event(event))
    if not enabled and valid_total < THRESHOLD:
        return {"enabled": False, "weights": dict(current), "updated_after": 0}
    if enabled and n_new == 0:
        return {"enabled": True, "weights": dict(current), "updated_after": previous_count}

    # сигнал: take → советнику +(если голосовал высоко) / −(если низко), trash → симметрично
    # наоборот. later нейтрален (не + не −). Есть ДВА режима расчёта сигнала:
    #
    # (А) PER-ADVISOR (Фаза 3 Feedback Cortex, 2026-07-24): если у события есть breakdown_votes,
    #     сигнал считается ПО КАЖДОМУ советнику отдельно через его собственный голос:
    #         signal[name] += action_sign × (advisor_score − 0.5) × 2
    #     где action_sign = +1 для take, −1 для trash. (advisor_score − 0.5) × 2 переводит [0,1] в
    #     [-1,1]: 0.95 → +0.9 (сильно за), 0.50 → 0 (нейтрален), 0.10 → −0.8 (сильно против).
    #     Смысл: советник, что высоко оценил МУСОР → штраф; низко оценил МУСОР → награда (был прав).
    #     Симметрично для take. Это и есть «наказывать/поощрять конкретного советника».
    #
    # (Б) FALLBACK (старая логика, backward compat): нет breakdown_votes → грубая эвристика по
    #     judged: solo → сигнал только rank_ideas; council → сигнал всем ALL_ADVISORS.
    #     «Судили все → поощряем/наказываем всех». Менее точно, но работает на старых данных.
    signal = {name: 0.0 for name in ALL_ADVISORS}
    for ev in batch:
        if not isinstance(ev, dict):
            continue
        action = ev.get("action")
        if action == "take":
            action_sign = 1.0
        elif action == "trash":
            action_sign = -1.0
        else:
            continue  # later нейтрально
        votes = ev.get("breakdown_votes")
        judged = ev.get("judged", "council")
        if isinstance(votes, dict) and votes:
            # (А) per-advisor: сигнал коррелирует голос советника с action юзера
            for name in ALL_ADVISORS:
                v = votes.get(name)
                if not isinstance(v, dict):
                    continue
                score = v.get("score")
                if isinstance(score, bool) or not isinstance(score, (int, float)):
                    continue
                score = float(score)
                if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                    continue
                # (score − 0.5) × 2 ∈ [-1, 1]; action_sign × это → направление сдвига веса
                signal[name] += action_sign * (score - 0.5) * 2.0
        elif judged == "solo":
            signal["rank_ideas"] += action_sign  # (Б) solo = судил один арбитр
        else:
            for name in ALL_ADVISORS:
                signal[name] += action_sign  # (Б) council = судили все
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

    # decay к равномерному при КАЖДОМ НОВОМ пересечении границы DECAY_EVERY.
    # Старые границы не применяем повторно на каждом часовом запуске.
    decay_steps = n // DECAY_EVERY - previous_count // DECAY_EVERY
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


def main(events_path=None):
    """Cron-точка входа: обработать только события после сохранённого updated_after.

    events_path инжектируется тестами; в бою по умолчанию читается журнал idea_engine.
    """
    import os
    import sys

    # idea_engine/data/triage_events.jsonl (там же где state.json)
    if events_path is None:
        ie_data = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "idea_engine", "data")
        events_path = os.path.join(ie_data, "triage_events.jsonl")
    if not os.path.exists(events_path):
        print("[feedback_cortex] нет triage_events.jsonl — нечего адаптировать")
        return
    idea_engine_dir = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "idea_engine")
    )
    sys.path.insert(0, idea_engine_dir)
    try:
        try:
            import triage_events

            original_events_path = triage_events.PATH
            try:
                triage_events.PATH = events_path
                events = triage_events.load()
            finally:
                triage_events.PATH = original_events_path
        except Exception as e:
            print(f"[feedback_cortex] не прочитать triage_events: {e}")
            return
    finally:
        if sys.path and sys.path[0] == idea_engine_dir:
            sys.path.pop(0)

    import council_weights

    state = council_weights.load()
    enabled = bool(state.get("enabled"))
    current = state["weights"] if enabled else council_weights.DEFAULT_WEIGHTS
    try:
        previous_count = max(0, int(state.get("updated_after", 0))) if enabled else 0
    except (TypeError, ValueError, OverflowError):
        previous_count = 0
    if previous_count > len(events):
        # Журнал мог быть восстановлен/очищен. Старые сигналы повторно не применяем:
        # сохраняем текущие веса и ставим курсор на фактический конец.
        previous_count = len(events)
    new_events = events[previous_count:]
    result = adapt_weights(
        new_events,
        dict(current),
        previous_count=previous_count,
        enabled=enabled,
    )
    council_weights.save(result)
    if result["enabled"] and not new_events:
        status = "без новых событий"
    elif result["enabled"]:
        status = f"активирован · новых событий: {len(new_events)}"
    else:
        valid_events = sum(1 for event in events if _is_triage_event(event))
        status = f"frozen (нужно {THRESHOLD} triage-действий, есть {valid_events})"
    print(f"[feedback_cortex] {status} · веса: {result['weights']}")


if __name__ == "__main__":
    main()
