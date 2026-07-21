"""Точка входа автосбора (main).

Вынесено из монолита harvest.py: одна зона — оркестровать цикл прогонов (гейт → опциональный
пропуск → cy.run → _save_sig → _log), собрать сводку в консоль. Всё, что нужно (env, гейт,
лог, органы, константы), читаем через фасад `import harvest` — и потому патчи в тестах/пульте
долетают до живого кода. Сам фасад harvest.py вызывает main() в `if __name__ == "__main__"`.
"""

import os


def main(argv):
    import bootstrap_paths
    import harvest

    # создать data dirs на свежем клоне (до всего остального)
    bootstrap_paths.ensure_data_dirs()

    # АВТО-ВОССТАНОВЛЕНИЕ state.json при повреждении (ДО backup_state, ДО органов).
    # Если state.json битый/отсутствует и есть валидный бэкап — восстанавливаем, шлём
    # CRITICAL-алерт, прогон продолжается как ни в чём не бывало. frozen store.py не трогаем
    # (проверка ВНЕ органов); повреждённый файл сохраняется как state.json.corrupted-<TS>.
    import alerts
    import config
    import recover_state

    recovery = recover_state.auto_recover_state_if_needed(config.IE_STATE_JSON, config.BACKUPS_DIR, config.MAX_BACKUPS)
    if recovery["recovered"]:
        alerts.maybe_alert(
            "CRITICAL",
            f"state.json был повреждён, автоматически восстановлен из бэкапа {recovery['backup_ts']}. "
            f"Повреждённый файл сохранён как state.json.corrupted-<TS> для разбора.",
        )

    force = "--force" in argv or "force" in argv  # ручной клик из пульта перебивает гейт
    nums = [a for a in argv if a.isdigit()]
    n = int(nums[0]) if nums else 1
    n = max(1, min(n, 50))  # предохранитель: не больше 50 прогонов за вызов
    goal = "приноси свежие идеи"  # та же цель/цепочка, что у ручной кнопки → deliver в общий инбокс
    # РЕЗЕРВНОЕ КОПИРОВАНИЕ state.json + seen_items.json перед прогоном (ОДИН раз за вызов main,
    # НЕ за каждый прогон в цикле — иначе N прогонов = N бэкапов под одним таймстемпом, а state.json
    # всё равно под state_lock и гонки внутри одного вызова нет). backup_state сам ротирует старые
    # (держит config.MAX_BACKUPS). Любая ошибка (нет файла/нет прав) — НЕ роняет прогон, только print.
    import backup

    backup.backup_state()
    env = harvest._harvest_env()
    mode = (
        (f"идеи={harvest.ask_llm._MODEL}" if harvest.ask_llm.available() else "идеи=stub (ключа нет)")
        + f" · источники={'+'.join(harvest._active_sources())} (бюджет {harvest.SOURCE_N})"
        + (" · force" if force else "")
    )

    cy = harvest.Cyborg(
        harvest.build_organs(), safe_mode=True, k=6
    )  # k>=6: роутер сурфейсит всю цепь (+readability_gate)
    total, skipped, total_dropped = 0, 0, 0
    try:
        for i in range(n):
            # гейт «есть что нового?» — не гоняем генератор впустую (а) на неизменной ленте ИЛИ
            # (б) на ленте, что перетасовалась, но всё «новое» мы уже разбирали раньше (fresh_n).
            # force (ручной клик) гейт перепрыгивает: юзер просит собрать СЕЙЧАС — и тогда отпечаток
            # даже не снимаем (иначе лишний fetch 31 заголовка ради результата, который всё равно игнорим).
            if force:
                sig, fresh_n, gate_out = None, None, None
            else:
                sig, _degraded, fresh_n, status, gate_out = harvest._source_signature()
                if status:
                    harvest._persist_status(status)  # живой статус источников для пульта (даже если прогон пропустим)
            if not harvest._should_run(sig, force, fresh_n):
                skipped += 1
                why = "нет новых items (уже разбирали)" if fresh_n == 0 else "источник не изменился"
                print(f"прогон {i + 1}/{n}: {why} — пропуск (без вызова LLM)")
                continue
            # переиспользуем items гейт-фетча (не тянем телегу второй раз за тик); force / сбой гейта →
            # gate_out=None → _run_collect фетчит сам, как раньше (фолбэк цел)
            run_env = {**env, "prefetched_out": gate_out} if isinstance(gate_out, dict) else env
            out = cy.run(goal, env=run_env)
            r = out.get("result")
            added = r if isinstance(r, int) else 0
            total += added
            total_dropped += int(out.get("dropped_stub") or 0)  # болванки, отсеянные доставкой за тик
            if sig is not None:
                harvest._save_sig(sig)  # запоминаем ленту только после реального прогона
            harvest._log(goal, out)
            dn = harvest._degrade_note(out)
            print(f"прогон {i + 1}/{n}: +{added} свежих идей в инбокс" + (f"  ⚠ {dn}" if dn else ""))
    except KeyboardInterrupt:
        print(f"\n[harvest] прерван на прогоне {i + 1}/{n}")
        return

    print(f"\n{mode}")
    line = f"ЗА ВЫЗОВ добавлено в инбокс: {total} | пропущено (лента не менялась): {skipped}"
    if total_dropped:  # шапка выше = конфиг-модель; тут ФАКТ: болванки = ключ есть, но сеть/парс подвели
        line += f" | ⚠ болванок отсеяно (сеть/парс LLM подводили): {total_dropped}"
    print(line)
    inbox_md = os.path.join(harvest._IE_DATA, "inbox.md")
    try:
        import store as _ie_store  # idea_engine/store.py (idea_engine уже в sys.path через wiring)

        open_n = len(_ie_store.Store(os.path.join(harvest._IE_DATA, "state.json"), cap=0).open_ideas())
        print(f"ВСЕГО в инбоксе (открытых идей): {open_n}")
    except Exception:
        pass
    print(f"инбокс (человеку): {inbox_md}")
