"""Фасад режима «ультра-идея» — кросс-источниковый синтез ОДНОЙ идеи (CLI + пульт).

Цепочка (главную дорожку ideate НЕ трогает, frozen-core не правится):
  collect (wiring._run_collect, под tg-замком)
  → pick_cross_sample (seed, по 1 материалу с каждого источника)
  → fuse_ideas (LLM-слияние + вратарь каркаса + ≤1 ремонтный вызов)
  → совет mind.deliberate (ТОЛЬКО балл; топ-K не нужен — идея одна)
  → readability_gate → scrub_secrets → deliver (инбокс, как у обычных идей)

Ключевые решения (обоснование — council 2026-08-15, .brain/councils/):
  * seen_items НЕ помечаем: материал остаётся доступен обычному ideate. Повтор
    выборки ловит combo_hash (sha1 по «source:id») — ре-ролл seed+1, до 3 попыток;
    в fusion_state.json пишется ТОЛЬКО после успешной доставки.
  * seed пишется в карточку: ре-ролл комбинации и итерации промпта воспроизводимы
    (сравниваем формулировки на одном материале, а не на шуме).
  * при score < keep_min_score карточка ВСЁ РАВНО доставляется с weak=True —
    кнопочный режим: тишина хуже слабой идеи; mass-purge <8 её потом достанет.
  * авто-гейт (--auto, только авто-петля пульта; ручная кнопка всегда гоняет):
    пул материалов не менялся с прошлой доставки → LLM не зовётся, тихий пропуск.
    Философия та же, что у harvest-гейта новизны: «нечего сплавлять — не сплавляем».
  * каждый прогон (успех/провал/гейт-скип) дописывает строку в fusion_runs.jsonl —
    калибровочная статистика «успех с 1-й попытки ≥60%» копится сама.

Запуск:
    python cyborg/fuse_mode.py                  — ультра-прогон (живой)
    python cyborg/fuse_mode.py --auto           — прогон от авто-петли (гейт пула)
    python cyborg/fuse_mode.py --dry-run        — показать выборку и промпт, БЕЗ LLM
    python cyborg/fuse_mode.py --seed 42        — воспроизвести конкретную комбинацию
    python cyborg/fuse_mode.py --json           — отчёт одной JSON-строкой (для пульта)
"""

import hashlib
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootstrap_paths  # noqa: E402

bootstrap_paths.ensure_project_paths()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config  # noqa: E402

# Кольцо combo_hash: файл не растёт бесконечно, старые выборки забываются.
MAX_COMBOS = 500
REROLL_ATTEMPTS = 3


def _state_path():
    return os.path.join(config.CYBORG_DATA_DIR, "fusion_state.json")


def _load_state():
    """{"combos": {hash: ts}, "last_seed": int}. Битого/нет файла → чистый дефолт."""
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (IOError, ValueError):
        return {"combos": {}, "last_seed": 0}


def _save_state(state):
    combos = state.get("combos", {})
    if len(combos) > MAX_COMBOS:  # обрезаем самые старые
        keep = sorted(combos.items(), key=lambda kv: kv[1], reverse=True)[:MAX_COMBOS]
        state["combos"] = dict(keep)
    path = _state_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _pool_signature(items):
    """Сигнатура пула материалов: sha1 по отсортированным «source:id».
    Порядок источников не важен — меняется состав пула, а не порядок сбора."""
    ids = sorted("%s:%s" % (it.get("source"), it.get("id")) for it in items)
    return hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()


def _gate_skip(state, pool_sig):
    """Авто-гейт: True, если пул не менялся с прошлой ДОСТАВЛЕННОЙ ультры.
    pool_sig пишется в состояние только при успехе — провал прошлой попытки
    не должен запирать следующую."""
    return bool(state.get("pool_sig")) and state["pool_sig"] == pool_sig


def _metrics_path():
    return os.path.join(config.CYBORG_DATA_DIR, "fusion_runs.jsonl")


def _log_run_metrics(rec):
    """Одна JSONL-строка на завершённый ультра-прогон (успех/провал/гейт-скип).
    Калибровка совета (успех с 1-й попытки ≥60%) читается прямо из файла.
    Сбой записи метрик НИКОГДА не роняет прогон — статистика вторична."""
    try:
        os.makedirs(os.path.dirname(_metrics_path()), exist_ok=True)
        with open(_metrics_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _score_single(card, env, op=None):
    """Балл совета для ОДНОЙ карточки (rank топ-K не нужен — идея одна).

    Тот же совет и тот же контекст, что у _rank_by_council (арбитр+интуиция+оркестр),
    но без короткого замыкания «len(ideas) <= keep» — нам нужен именно score.
    Совет упал/промолчал → score=None + score_error: доставка НЕ блокируется.
    Возвращает карточку с score (0..10, как бейдж совета у обычных идей).
    """
    import wiring
    from wiring_council import _anti_bland_scores, _council_no_cap
    from wiring_runtime import _content_llm

    orch = env.get("orchestra")
    if isinstance(orch, dict) and orch.get("models"):
        orch = {
            **orch,
            "max_workers": len(orch["models"]),
            "timeout_sec": int(env.get("orchestra_timeout_sec", config.WIRING_COUNCIL_ORCHESTRA_TIMEOUT_SEC)),
        }
    context = {
        "content_llm": _content_llm(env),
        "llm_chain": env.get("llm_chain"),
        "orchestra": orch,
        "llm_timeout_ms": env.get("llm_timeout_ms", config.WIRING_COUNCIL_LLM_TIMEOUT_MS),
        "direction": env.get("direction"),
    }
    question = (
        "Отбери лучшие идеи для доставки: доказательность по исходным материалам, "
        "конкретная проверка пользы, практичность, оригинальность и выполнимость."
    )
    if env.get("direction"):
        question += f" Приоритет — идеи в направлении «{env['direction']}»."
    if callable(op):
        n_rev = len(orch["models"]) if isinstance(orch, dict) and orch.get("models") else 0
        op("совет судит ультра-идею%s" % (" (%d рецензентов)" % n_rev) if n_rev else "совет судит ультра-идею")
    card = dict(card)
    try:
        verdict = wiring.mind.deliberate(question, [{**card, "id": 0}], _council_no_cap(context), context)
        live = verdict.get("live") or []
        scores = _anti_bland_scores(verdict.get("scores") or {}, verdict.get("breakdown"))
        sc = scores.get(0, scores.get("0"))
        if verdict.get("degraded") or not live or sc is None:
            card["score"] = None
            card["score_error"] = "совет промолчал (degraded)"
            return card
        card["score"] = round(float(sc) * 10, 1)  # 0..1 → 0..10, как у обычных идей (D6)
        card["judged"] = "solo" if len(live) < 2 else "council"
    except Exception as exc:  # совет НИКОГДА не роняет доставку
        card["score"] = None
        card["score_error"] = str(exc)[:200]
    return card


def run_fusion(seed=None, direction=None, oversample=1, dry_run=False, auto=False, on_progress=None):
    """Одноразовый ультра-прогон + строка калибровочных метрик. Отчёт — наружу."""
    rep = _run_fusion_impl(
        seed=seed,
        direction=direction,
        oversample=oversample,
        dry_run=dry_run,
        auto=auto,
        on_progress=on_progress,
    )
    if not dry_run:  # dry-run — не прогон, в статистику не идёт
        card = rep.get("card") or {}
        _log_run_metrics(
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "auto": bool(auto),
                "ok": bool(rep.get("ok")),
                "skipped": rep.get("skipped"),
                "reason": rep.get("reason"),
                "seed": rep.get("seed"),
                "combo_hash": rep.get("combo_hash") or card.get("combo_hash"),
                "attempts": rep.get("attempts"),
                "score": card.get("score"),
                "weak": bool(card.get("weak")),
            }
        )
    return rep


def _run_fusion_impl(seed=None, direction=None, oversample=1, dry_run=False, auto=False, on_progress=None):
    """Сам прогон (метрики дописывает обёртка run_fusion)."""
    import harvest_env
    import seen_items
    import wiring
    from organs import fuse_ideas, pick_cross_sample
    from wiring_runtime import _content_llm

    op = on_progress if callable(on_progress) else (lambda _msg: None)
    env = harvest_env._harvest_env()  # тот же источник/совет, что у обеих кнопок
    if direction:
        env["direction"] = direction  # CLI-руль перекрывает сохранённый
    env["on_progress"] = op
    state = _load_state()
    seed = int(seed if seed is not None else random.SystemRandom().randrange(1 << 30))

    op("собираю сырьё (%s)" % ", ".join(env.get("sources") or []))
    out = wiring._run_collect({}, env)
    items = out.get("items") or []
    if not items:
        return {"ok": False, "reason": "сырьё пустое (все источники промолчали?)", "seed": seed}

    pool_sig = _pool_signature(items)
    if auto and _gate_skip(state, pool_sig):
        # авто-петля: материала нет нового — LLM не зовём (ручная кнопка сюда не попадает)
        op("пул материалов не менялся с прошлой ультры — авто-пропуск")
        return {
            "ok": True,
            "skipped": "pool_unchanged",
            "reason": "пул материалов не менялся с прошлой ультры — авто-прогон пропущен",
            "seed": seed,
        }

    # Рулетка предпочитает НЕразобранное: used_ids — те же «source:id», что seen_items.
    used = set(seen_items.load().keys())

    pick, used_seed, repeated = None, seed, False
    for attempt in range(REROLL_ATTEMPTS):  # ре-ролл ТОЛЬКО на повтор выборки
        used_seed = seed + attempt
        pick = pick_cross_sample.run(
            {
                "items": items,
                "seed": used_seed,
                "oversample": oversample,
                "used_ids": used,
                "sources_expected": env.get("sources") or [],
            },
            env,
        )
        if pick["skip"]:
            return {
                "ok": False,
                "reason": pick["skip"],
                "seed": used_seed,
                "sources_available": pick["sources_available"],
            }
        if fuse_ideas._combo_hash(pick["picked"]) not in state.get("combos", {}):
            break
        repeated = attempt == REROLL_ATTEMPTS - 1  # все попытки совпали — идём с последней

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "seed": used_seed,
            "picked": pick["picked"],
            "sources_missing": pick["sources_missing"],
            "reused_ids": pick["reused_ids"],
            "combo_hash": fuse_ideas._combo_hash(pick["picked"]),
            "prompt": fuse_ideas.build_prompt(
                pick["picked"],
                direction=env.get("direction"),
                rejected=env.get("rejected") or [],
                select_from_pairs=oversample > 1,
            ),
        }

    op("слияние: %d материалов → 1 идея" % len(pick["picked"]))
    llm = _content_llm(env)
    if llm is not None:
        import ask_llm

        if llm is ask_llm.ask:
            # Слиянию нужна точность схемы, не раскованность: низкая температура — тот же
            # приём, что у readability score_llm. Чужой llm (тест/stub) проходит как есть.
            llm = lambda p: ask_llm.ask(p, temperature=config.FUSE_TEMPERATURE)
    fout = fuse_ideas.run(
        {
            "picked": pick["picked"],
            "seed": used_seed,
            "direction": env.get("direction"),
            "rejected": env.get("rejected") or [],
            "sources_missing": pick["sources_missing"],
            "select_from_pairs": oversample > 1,
            "repair_retries": 2,  # живые прогоны: послушание схемы — узкое место, 3 попытки дешевле провала
        },
        {"llm": llm},
    )
    if not fout["ideas"]:
        return {
            "ok": False,
            "reason": fout["fusion_skip"],
            "seed": used_seed,
            "violations": fout.get("violations", []),
            "attempts": fout.get("attempts", 0),
            "raw_tail": fout.get("raw_tail", ""),
            "raw": fout.get("raw", ""),
        }

    card = fout["ideas"][0]
    card = _score_single(card, env, op)
    if card.get("score") is not None:
        # weak: ниже порога совета — доставляем с пометкой (тишина хуже слабой идеи)
        if float(card["score"]) / 10.0 < float(env.get("keep_min_score", config.DEFAULT_KEEP_MIN_SCORE)):
            card["weak"] = True

    # Дальше — обычные рельсы главной дорожки: читаемость → чистка → доставка.
    op("полировка и доставка")
    polished = wiring_council_readability({"ideas_best": [card]}, env)
    safe = wiring._run_scrub({"ideas_polished": polished.get("ideas_polished") or [card]}, env)
    delivered = wiring._run_deliver({"ideas_safe": safe.get("ideas_safe") or [card]}, env)

    # deliver.run отдаёт счётчик под ключом "delivered" (не "added" — соседние sink'и
    # отличаются; ловится только живой проверкой, тестами с моком deliver не накрыть)
    added = int(delivered.get("delivered", 0)) if isinstance(delivered, dict) else 0
    if not added and isinstance(delivered, dict):
        return {
            "ok": False,
            "reason": "доставка отклонила карточку (dup=%s stub=%s brain_down=%s)"
            % (delivered.get("dropped_dup"), delivered.get("dropped_stub"), delivered.get("brain_down")),
            "seed": used_seed,
            "card": card,
            "delivered": delivered,
        }
    if added > 0:  # combo_hash сгорает только у ДОСТАВЛЕННОЙ комбинации
        state.setdefault("combos", {})[card["combo_hash"]] = int(time.time())
        state["last_seed"] = used_seed
        state["pool_sig"] = pool_sig  # авто-гейт меряет от последнего успеха (любого запуска)
        _save_state(state)

    return {
        "ok": added > 0,
        "card": card,
        "seed": used_seed,
        "combo_hash": card["combo_hash"],
        "attempts": fout["attempts"],
        "sources_used": card.get("sources_used"),
        "sources_missing": card.get("sources_missing"),
        "reused_ids": pick["reused_ids"],
        "combo_repeated": repeated,
        "delivered": delivered,
    }


def wiring_council_readability(inputs, env):
    """readability_gate через обёртку wiring (патчится в тестах как wiring._run_readability)."""
    import wiring

    return wiring._run_readability(inputs, env)


def format_report(rep):
    """Человеческая сводка для CLI."""
    lines = []
    if rep.get("dry_run"):
        lines.append("DRY-RUN: LLM не звался, доставка не было.")
        lines.append("  seed=%s combo=%s" % (rep.get("seed"), rep.get("combo_hash")))
        for it in rep.get("picked") or []:
            lines.append("  [%s] %s" % (it.get("source"), it.get("title")))
        if rep.get("sources_missing"):
            lines.append("  красные источники: %s" % ", ".join(rep["sources_missing"]))
        if rep.get("reused_ids"):
            lines.append("  повторно использованы: %s" % ", ".join(rep["reused_ids"]))
        lines.append("--- промпт слияния ---")
        lines.append(rep.get("prompt") or "")
        return lines
    if rep.get("skipped"):
        lines.append("АВТО-ГАЙТ: прогон пропущен.")
        lines.append("  Причина: %s" % (rep.get("reason") or rep["skipped"]))
        return lines
    if not rep.get("ok"):
        lines.append("Ультра-идея не удалась.")
        if rep.get("reason"):
            lines.append("  Причина: %s" % rep["reason"])
        if rep.get("violations"):
            lines.append("  Вратарь: %s" % "; ".join(rep["violations"][:5]))
        if rep.get("raw_tail"):
            lines.append("  Хвост сырого ответа: …%s" % rep["raw_tail"][-200:])
        if rep.get("raw"):
            lines.append("--- сырой ответ (первые 1500) ---")
            lines.append(rep["raw"][:1500])
        if rep.get("seed") is not None:
            lines.append("  seed=%s" % rep["seed"])
        return lines
    card = rep.get("card") or {}
    lines.append("✓ Ультра-идея доставлена (seed=%s, попыток слияния: %s)." % (rep.get("seed"), rep.get("attempts")))
    lines.append("  Заголовок: %s" % card.get("title"))
    lines.append("  Оценка совета: %s%s" % (card.get("score"), " (weak)" if card.get("weak") else ""))
    for f in card.get("fusion") or []:
        lines.append(
            "  [%s/%s] взяли: %s — без него: %s" % (f.get("source"), f.get("role"), f.get("took"), f.get("collapse"))
        )
    if rep.get("sources_missing"):
        lines.append("  Красные источники: %s" % ", ".join(rep["sources_missing"]))
    if rep.get("combo_repeated"):
        lines.append("  ⚠ комбинация уже встречалась (все ре-роллы совпали)")
    return lines


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="kiborg: режим ультра-идеи (кросс-источниковый синтез)")
    ap.add_argument("--seed", type=int, default=None, help="воспроизвести конкретную выборку")
    ap.add_argument("--direction", default=None, help="руль темы (перекрывает сохранённый)")
    ap.add_argument("--oversample", type=int, default=1, help="кандидатов на источник (2 = LLM выбирает из пары)")
    ap.add_argument("--dry-run", action="store_true", help="показать выборку и промпт, БЕЗ LLM и доставки")
    ap.add_argument("--auto", action="store_true", help="прогон от авто-петли: гейт «пул не менялся → пропуск»")
    ap.add_argument("--json", action="store_true", help="отчёт одной JSON-строкой")
    args = ap.parse_args(argv)

    rep = run_fusion(
        seed=args.seed,
        direction=args.direction,
        oversample=max(1, args.oversample),
        dry_run=args.dry_run,
        auto=args.auto,
        on_progress=lambda m: print("[fuse] %s" % m),
    )
    if args.json:
        safe = dict(rep)
        safe.pop("prompt", None)  # промпт огромный, в JSON-отчёте не нужен
        safe.pop("raw", None)  # сырой ответ тоже; для калибровки есть человекочитаемый вывод
        print(json.dumps(safe, ensure_ascii=False))
    else:
        for line in format_report(rep):
            print(line)
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
