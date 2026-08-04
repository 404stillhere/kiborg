"""Фасад Oracle-режима для CLI и пульта.

Готовит env для фиксированной цепочки oracle_scan → oracle_plan → deliver_oracle
и печатает человеческий отчёт о сохранённом плане.
"""

import os

PROJECTS_ROOT = "M:/projects"


def prepare_env(goal, project_path, projects_root=None):
    """Возвращает env-набор для oracle-цепочки.

    project_path: абсолютный путь к проекту (может не существовать — oracle_scan сам вернёт ошибку).
    goal: текст цели, например «добавить авторизацию».
    """
    env = {"mode": "oracle"}
    env["oracle_project"] = project_path
    env["oracle_goal"] = goal
    env["projects_root"] = projects_root or PROJECTS_ROOT
    return env


def run_oracle(goal, project_path, *, on_step=None, safe_mode=True, projects_root=None):
    """Одноразовый прогон Oracle-режима. Возвращает dict результата оркестратора."""
    import ask_llm
    from orchestrator import Cyborg
    from wiring import build_organs

    env = prepare_env(goal, project_path, projects_root)
    if ask_llm.available():
        env["content_llm"] = ask_llm.ask
    cy = Cyborg(build_organs(), safe_mode=safe_mode)
    return cy.run(goal, env=env, on_step=on_step)


def format_result(out):
    """Человеческая сводка для CLI/пульта."""
    lines = []
    delivered = out.get("result") or {}
    if delivered.get("ok"):
        lines.append("✓ План сохранён.")
        lines.append(f"  Проект: {delivered.get('slug')}")
        lines.append(f"  План:   {delivered.get('plan_path')}")
        lines.append(f"  Индекс: {delivered.get('index_path')}")
    else:
        lines.append("Oracle не удался.")
        err = delivered.get("error") if isinstance(delivered, dict) else None
        if err:
            lines.append(f"  Ошибка: {err}")
    lines.append("Трасса:")
    for t in out.get("trace", []):
        organ = t.get("organ")
        if not organ:
            continue
        mark = "✓"
        if t.get("error"):
            mark = "✗"
        elif t.get("skipped"):
            mark = "-"
        lines.append(f"  {mark} {organ}: {t.get('why', '')}")
    return "\n".join(lines)


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(description="Oracle: построить план от точки А до точки Б для локального проекта.")
    parser.add_argument("--project", required=True, help="Путь к локальному проекту")
    parser.add_argument("--goal", required=True, help="Цель, например: добавить авторизацию")
    parser.add_argument("--root", default=PROJECTS_ROOT, help=f"Корень проектов (default: {PROJECTS_ROOT})")
    args = parser.parse_args(argv)

    goal = args.goal.strip()
    if not goal:
        parser.error("укажите цель — что нужно получить")
    project_path = os.path.abspath(args.project)

    def _on_step(step, phase, name, why):
        tag = {"start": "⏳", "done": "✓", "finish": "🏁"}.get(phase, phase)
        tail = f" — {why}" if why else ""
        print(f"  {tag} {name}{tail}", flush=True)

    print(f"Oracle: «{goal}» → {project_path}", flush=True)
    out = run_oracle(goal, project_path, on_step=_on_step, projects_root=args.root)
    print(format_result(out))
    return 0 if (out.get("result") or {}).get("ok") else 1


if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main(sys.argv[1:]))
