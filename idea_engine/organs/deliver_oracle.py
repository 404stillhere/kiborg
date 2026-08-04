"""Орган-приёмник Oracle: сохраняет план в отдельный файл и кладёт карточку в инбокс.

Контракт: run(inputs, env) -> {"ok": bool, "plan_path": str, "index_path": str, "inbox_card": bool}.
Входы:
  inputs["plan"] — результат oracle_plan.
  env["oracle_project"] — путь к проекту (для slug).
  env["oracle_goal"] — цель.
"""

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from cyborg import config  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ORACLES_DIR = DATA_DIR / "oracles"
INBOX_PATH = DATA_DIR / "inbox.md"
INDEX_PATH = ORACLES_DIR / config.ORACLE_INDEX_FILE

# План с той же целью/проектом, созданный не позднее этого окна, считается дубликатом.
DEDUP_WINDOW_HOURS = config.ORACLE_DEDUP_WINDOW_HOURS


def _slug(text):
    base = os.path.basename(text) or "oracle"
    base = re.sub(r"[^\w\-]+", "-", base)
    base = base.strip("-").lower()
    return base or "oracle"


def _goal_fingerprint(goal):
    """Нормализованная цель для сравнения: нижний регистр, только буквы/цифры/пробелы."""
    return re.sub(r"[^\w\s]+", " ", goal.lower()).strip()


def _find_duplicate(slug, goal, since):
    """Найти существующий план с тем же slug и похожей целью, созданный после since."""
    plan_dir = ORACLES_DIR / slug
    if not plan_dir.is_dir():
        return None
    goal_fp = _goal_fingerprint(goal)
    best = None
    for path in plan_dir.glob(f"*{config.ORACLE_PLAN_EXT}"):
        if not path.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if mtime < since:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"\*\*Цель:\*\*\s*(.+?)(?:\r?\n|\r)", text)
        if not m:
            continue
        existing_fp = _goal_fingerprint(m.group(1))
        if existing_fp == goal_fp:
            return path
        # если не точное совпадение — запоминаем самый свежий для возможного перезаписи
        if best is None or mtime > best[1]:
            best = (path, mtime)
    return best[0] if best else None


def run(inputs, env):
    plan = (inputs or {}).get("plan")
    if not plan or not isinstance(plan, dict):
        return {"ok": False, "error": "plan missing"}

    project = str(env.get("oracle_project", "")).strip()
    goal = str(env.get("oracle_goal", "")).strip()
    slug = _slug(project or plan.get("title", "oracle"))
    now = datetime.now()
    date = now.strftime(config.ORACLE_PLAN_DATE_FMT)
    time_ = now.strftime(config.ORACLE_PLAN_TIME_FMT)
    plan_dir = ORACLES_DIR / slug
    plan_path = plan_dir / f"{date}_{time_}{config.ORACLE_PLAN_EXT}"

    os.makedirs(plan_dir, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    text = _render_plan(plan, goal, project)

    since = now - timedelta(hours=DEDUP_WINDOW_HOURS)
    duplicate = _find_duplicate(slug, goal, since)
    if duplicate:
        plan_path = duplicate

    _atomic_write(plan_path, text)

    index_entry = _index_entry(slug, plan, goal, plan_path)
    _append_to_index(index_entry)

    inbox_ok = _append_inbox_card(slug, plan, goal, plan_path)

    return {
        "ok": True,
        "plan_path": str(plan_path),
        "index_path": str(INDEX_PATH),
        "inbox_card": inbox_ok,
        "slug": slug,
        "replaced": duplicate is not None,
    }


def _render_plan(plan, goal, project):
    lines = [
        f"# {plan.get('title', 'План')}",
        "",
        f"**Цель:** {goal}",
        f"**Проект:** {project}",
        f"**Создан:** {datetime.now().strftime(config.ORACLE_PLAN_INDEX_FMT)}",
        "",
        "## Краткое описание",
        plan.get("summary", "") or "(без описания)",
        "",
        "## Шаги",
    ]
    for step in plan.get("steps", []):
        effort = step.get("effort", "?")
        deps = step.get("depends_on", []) or []
        files = step.get("files", []) or []
        lines.append(f"### {step.get('id', '?')} — {step.get('title', '')} [{effort}]")
        lines.append(step.get("description", "") or "")
        if files:
            lines.append(f"**Файлы:** {', '.join(files)}")
        if deps:
            lines.append(f"**Зависит от:** {', '.join(deps)}")
        lines.append(f"**Проверка:** {step.get('verification', '') or '—'}")
        lines.append("")

    risks = plan.get("risks", [])
    if risks:
        lines.append("## Риски")
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")

    warnings = plan.get("warnings", [])
    if warnings:
        lines.append("## Предупреждения")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("---")
    lines.append("_Сгенерировано Oracle / kiborg_")
    lines.append("")
    return "\n".join(lines)


def _index_entry(slug, plan, goal, plan_path):
    date = datetime.now().strftime(config.ORACLE_PLAN_INDEX_FMT)
    title = plan.get("title", "План")
    steps = len(plan.get("steps", []))
    return f"- [{title}]({slug}/{os.path.basename(plan_path)}) " f"— {goal} ({steps} шагов, {date})"


def _append_to_index(entry):
    os.makedirs(ORACLES_DIR, exist_ok=True)
    if not INDEX_PATH.exists():
        header = "# Индекс планов Oracle\n\n"
        _atomic_write(INDEX_PATH, header + entry + "\n")
    else:
        with open(INDEX_PATH, "a", encoding="utf-8") as f:
            f.write(entry + "\n")


def _append_inbox_card(slug, plan, goal, plan_path):
    try:
        card = (
            f"\n- **Oracle** [{len(plan.get('steps', []))} шагов] "
            f"{plan.get('title', goal)} — `{_short(goal)}`\n"
            f"    - проект: `{os.path.basename(plan_path.parent)}`\n"
            f"    - план: `{plan_path}`\n"
        )
        with open(INBOX_PATH, "a", encoding="utf-8") as f:
            f.write(card)
        return True
    except Exception:
        return False


def _short(text, max_len=60):
    text = text.replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _atomic_write(path, text):
    os.makedirs(Path(path).parent, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
