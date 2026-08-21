"""Орган: finish_step — режим Б: «самый маленький шаг, чтобы ДОДЕЛАТЬ существующее».

Контракт: run(inputs, env) -> {"nudge": {...}|None, ...}.
Читает карту проектов (recon.json панели) — путь через env["recon_path"];
сам глобально никуда не лезет. Выбирает недоделанный проект и достаёт его next_step.
Ротация — по env["cursor"], чтобы не долбить один и тот же проект каждый раз.
"""

import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from cyborg import config  # noqa: E402


def _load(path):
    with open(path, encoding=config.HTTP_CHARSET_UTF8) as f:
        return json.load(f)


def run(inputs, env):
    env = env or {}
    path = env.get("recon_path")
    cursor = int(env.get("cursor", 0))
    skip = set(env.get("skip_folders", []))
    if not path:
        return {"nudge": None, "error": "no recon_path"}
    try:
        cards = _load(path)
    except Exception as e:
        return {"nudge": None, "error": str(e)}

    cand = []
    for c in cards:
        if not isinstance(c, dict) or c.get("folder") in skip:
            continue
        state = (c.get("state") or "").lower()
        if state in ("dead", "abandoned"):
            continue
        if not c.get("next_step"):
            continue
        cand.append(c)

    if not cand:
        return {"nudge": None, "pool": 0}

    c = cand[cursor % len(cand)]
    return {
        "nudge": {
            "title": f"Доделать: {c.get('folder')}",
            "why": (c.get("next_step") or "")[: config.FINISH_STEP_WHY_MAX_CHARS],
            "effort": "средне",
            "kind": "finish",
            "folder": c.get("folder"),
            "state": c.get("state"),
        },
        "next_cursor": cursor + 1,
        "pool": len(cand),
    }


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cyborg"))
    import config  # noqa: E402

    p = sys.argv[1] if len(sys.argv) > 1 else config.RECON_FILE
    print(json.dumps(run({}, {"recon_path": p}), ensure_ascii=False, indent=2))
