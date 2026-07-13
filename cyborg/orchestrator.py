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

    def run(self, goal, env=None):
        env = dict(env or {})
        mem = Memory()
        env["memory"] = mem.data
        deliverable = brain_mod.infer_deliverable(goal, self.organs)
        trace = []
        for step in range(self.max_steps):
            candidates = router_mod.route(goal, self.organs, self.k)
            decision = brain_mod.plan(goal, candidates, mem, env, organs_all=self.organs)
            if decision["action"] == "finish":
                trace.append({"step": step, "action": "finish", "why": decision["why"]})
                break
            organ = decision["organ"]
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
        return {
            "goal": goal,
            "deliverable": deliverable,
            "result": mem.data.get(deliverable) if deliverable else None,
            "council": mem.data.get("council"),  # метаданные совещания на отборе (если совет судил)
            "memory_keys": list(mem.data.keys()),
            "trace": trace,
            "steps": len(trace),
            "routed": [o.name for o in router_mod.route(goal, self.organs, self.k)],
        }
