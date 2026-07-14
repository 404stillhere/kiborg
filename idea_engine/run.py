"""Оболочка первого среза киборга «приносит идеи».

Один tick:
  - в дорожке A есть место -> РЕЖИМ A: collect -> ideate -> добить дорожку до потолка;
  - места нет (полна) -> РЕЖИМ B: обновить слот «доделать существующее».
Всегда пишет inbox.md (для человека) и дописывает NOTIFY.md (файловое «уведомление»;
ТГ-пуш — следующим шагом, ему нужен твой бот/чат).

Оболочка — единственное место, что знает про пути/источники; органы остаются чистыми.

CLI:
  python run.py tick [--seed FILE]      — один шаг (FILE = строки JSON, играет роль ask_llm)
  python run.py status <id> <take|later|trash>
  python run.py show
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import Store, state_lock  # noqa: E402
from organs import collect_source, ideate, finish_step  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
STATE = os.path.join(DATA, "state.json")
INBOX = os.path.join(DATA, "inbox.md")
NOTIFY = os.path.join(DATA, "notify.md")

CFG = {
    "cap": 0,              # 0 = без потолка: идеи копятся в одну кучу, разбираешь в своём темпе
    "n": 8,                # (только legacy standalone-tick; живой конвейер берёт n из harvest.SOURCE_N)
    "source": "hn",
    "k": 3,                # сколько идей за раз
    "recon_path": "M:/projects/panelofprojects/recon.json",
    "skip_folders": [],    # folder'ы режима B, которые не толкать (пусто = не фильтровать); knob finish_step
}


def _seed_brain(seed_path):
    """Файл со строками JSON -> callable(prompt)->str. Стенд-ин ask_llm до ключа."""
    with open(seed_path, encoding="utf-8") as f:
        blob = f.read()
    return lambda _prompt: blob


def tick(store, seed_path=None):
    store.data["tick"] += 1
    if store.has_room():
        raw = collect_source.run({}, {"n": CFG["n"], "source": CFG["source"]})
        env = {"k": CFG["k"]}
        if seed_path:
            env["llm"] = _seed_brain(seed_path)
        out = ideate.run({"items": raw["items"]}, env)
        added, brains = 0, set()
        for idea in out["ideas"]:
            idea.setdefault("kind", "new")
            idea["source"] = raw.get("source")
            if store.add_idea(idea):
                added += 1
                brains.add(idea.get("brain", "?"))
            if not store.has_room():
                break
        info = {"mode": "A", "added": added, "brain": ",".join(sorted(brains)) or "-",
                "degraded": raw.get("degraded", False)}
    else:
        out = finish_step.run({}, {"recon_path": CFG["recon_path"], "cursor": store.data["cursor"],
                                   "skip_folders": CFG["skip_folders"]})
        if out.get("nudge"):
            store.set_finish(out["nudge"], out.get("next_cursor", store.data["cursor"]))
        info = {"mode": "B", "nudge": bool(out.get("nudge")), "pool": out.get("pool")}

    store.save()
    _write_inbox(store)
    _notify(store, info)
    return info


def _write_inbox(store):
    d = store.data
    lines = ["# Инбокс идей киборга", ""]
    op = store.open_ideas()
    cap = d.get("cap") or 0
    cap_txt = "без потолка" if cap in (0, None) else f"потолок {cap}"
    lines.append(f"Идей в разборе: {len(op)} ({cap_txt}) | tick: {d['tick']} | разобрано: {store.cleared_count()}")
    lines.append("")
    lines.append("## Дорожка A — новые идеи (разбирай: взять / позже / мусор)")
    if not op:
        lines.append("_пусто — киборг принесёт на следующем сборе_")
    for i in op:
        lines.append(f"- **#{i['id']}** [{i.get('effort', '?')}] {i.get('title', '')}")
        if i.get("why"):
            lines.append(f"    - {i['why']}")
        lines.append(f"    - _мозг: {i.get('brain', '?')} · разобрать: `python run.py status {i['id']} take|later|trash`_")
    lines.append("")
    lines.append("## Дорожка B — доделать существующее (когда идеи заполнены)")
    fin = d.get("finish")
    if fin:
        lines.append(f"- [{fin.get('effort', '?')}] **{fin.get('title', '')}**")
        lines.append(f"    - {fin.get('why', '')}")
    else:
        lines.append("_пока пусто_")
    lines.append("")
    os.makedirs(DATA, exist_ok=True)
    with open(INBOX, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _notify(store, info):
    os.makedirs(DATA, exist_ok=True)
    t = store.data["tick"]
    if info["mode"] == "A":
        msg = f"tick {t}: режим A — добавлено идей {info['added']} (мозг {info['brain']}{', DEGRADED' if info['degraded'] else ''})"
    else:
        msg = f"tick {t}: режим B — очередь полна, напоминание доделать ({'есть' if info['nudge'] else 'нет'})"
    with open(NOTIFY, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def _cli(argv):
    if not argv:
        print(__doc__)
        return
    cmd = argv[0]
    if cmd == "tick":
        seed = None
        if "--seed" in argv:
            seed = argv[argv.index("--seed") + 1]
        with state_lock(STATE):        # замок вокруг load→save (другой процесс не затрёт state.json)
            store = Store(STATE, cap=CFG["cap"])
            info = tick(store, seed_path=seed)
        print("TICK", info)
        print("inbox ->", INBOX)
    elif cmd == "status":
        idea_id, st = int(argv[1]), argv[2]
        if st not in ("take", "later", "trash"):
            print("статус должен быть take|later|trash")
            return
        with state_lock(STATE):        # триаж пульта: замок вокруг load→set_status→save
            store = Store(STATE, cap=CFG["cap"])
            ok = store.set_status(idea_id, st)
            store.save()
            _write_inbox(store)
        print("OK" if ok else "NOT_FOUND", f"#{idea_id} -> {st}")
    elif cmd == "show":
        print(open(INBOX, encoding="utf-8").read() if os.path.exists(INBOX) else "(инбокса ещё нет)")
    else:
        print("неизвестная команда:", cmd)
        print(__doc__)


if __name__ == "__main__":
    _cli(sys.argv[1:])
