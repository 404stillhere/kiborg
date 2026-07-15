"""Оболочка-оркестратор киборга (бета). Агентный цикл по вердикту совета 5 моделей:

  цель → РОУТЕР отбирает подмножество органов (не все разом) → МОЗГ выбирает
  следующий орган → ИСПОЛНИТЕЛЬ безопасно вызывает → результат в ПАМЯТЬ (env.memory) →
  повтор → результат. Ошибки/гейты не роняют цикл (перепланирование через memory.blocked).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import router as router_mod  # noqa: E402
import brain as brain_mod  # noqa: E402
import executor as executor_mod  # noqa: E402
from core import Memory  # noqa: E402


class Cyborg:
    def __init__(self, organs, safe_mode=True, max_steps=8, k=5):
        self.organs = organs
        self.safe_mode = safe_mode
        self.max_steps = max_steps
        self.k = k

    def run(self, goal, env=None, on_step=None):
        """on_step(step, phase, name, why) — опциональный колбэк ЖИВОГО прогресса (default None =
        поведение не меняется). Зовётся при СТАРТЕ каждого органа (phase='start') и по его
        завершении (phase='done'). Нужен, чтобы пульт/CLI показывали, что киборг делает ПРЯМО
        СЕЙЧАС: конвейер с живыми моделями идёт минуты, без этого консоль молчит и кажется завис."""
        env = dict(env or {})
        mem = Memory()
        env["memory"] = mem.data
        deliverable = brain_mod.infer_deliverable(goal, self.organs)
        trace = []

        def _emit(step, phase, name, why):
            if on_step:
                try:
                    on_step(step, phase, name, why)
                except Exception:
                    pass   # прогресс — удобство; сбой колбэка не роняет прогон

        for step in range(self.max_steps):
            candidates = router_mod.route(goal, self.organs, self.k)
            decision = brain_mod.plan(goal, candidates, mem, env, organs_all=self.organs)
            if decision["action"] == "finish":
                _emit(step, "finish", "", decision["why"])
                trace.append({"step": step, "action": "finish", "why": decision["why"]})
                break
            organ = decision["organ"]
            _emit(step, "start", organ.name, decision["why"])   # «сейчас работаю над …»
            result = executor_mod.execute(organ, decision["inputs"], env, self.safe_mode)
            note = mem.observe(organ.name, result)
            # страховка от холостого спина: орган отработал, но НИ ОДНОГО своего produces-ключа
            # не записал (вернул {} / чужие ключи) -> в blocked, чтобы не переизбирать бесконечно.
            if not note.get("error") and not note.get("skipped"):
                if organ.produces and not (set(organ.produces) & set(note.get("keys") or [])):
                    mem.blocked.add(organ.name)
            trace.append({"step": step, "organ": organ.name, "why": decision["why"],
                          "got": note.get("keys"), "error": note.get("error"),
                          "skipped": note.get("skipped")})
            _emit(step, "done", organ.name, note.get("error") or note.get("skipped") or "")
        return {
            "goal": goal,
            "deliverable": deliverable,
            "result": mem.data.get(deliverable) if deliverable else None,
            "council": mem.data.get("council"),  # метаданные совещания на отборе (если совет судил)
            "memory_keys": list(mem.data.keys()),
            "trace": trace,
            "steps": len(trace),
            "routed": [o.name for o in router_mod.route(goal, self.organs, self.k)],
            # сигналы деградации наружу (root #1: провенанс не только ВНУТРИ mem.data, но и в
            # выхлопе — чтобы лог/пульт показывали «источник в фолбэке» / «отсеяно болванок»,
            # а не рапортовали здоровье на мусоре). observe() кладёт ВСЕ ключи результата органа.
            "degraded": bool(mem.data.get("degraded")),
            "dropped_stub": int(mem.data.get("dropped_stub") or 0),
        }
