"""Орган Oracle: строит план по цели и карте проекта через LLM.

Контракт: run(inputs, env) -> {"plan": {...}}.
Входы:
  inputs["project_map"] — результат oracle_scan.
  env["oracle_goal"]    — цель, которую нужно достичь.
  env["llm"]            — callable(prompt) -> str (опционально).

Выход plan:
  title, summary, steps[{id, title, description, files, effort, depends_on, verification}],
  risks, warnings.
"""

import json
import re

PROMPT_TMPL = """Ты технический планировщик. У тебя есть карта проекта и цель.
Построй пошаговый план от текущего состояния проекта к цели.

Цель: {goal}

Карта проекта:
Название: {name}
Корневая папка: {root}
Описание из README: {readme_summary}
Инбокс: {inbox_state}
Файлы: {files}
Точки входа: {entrypoints}
Маркеры проблем: {markers}
Расширения файлов: {extensions}

Требования к плану:
1. Каждый шаг должен быть конкретным и выполнимым.
2. Поле files должно содержать только файлы, которые реально есть в списке "Файлы".
3. Effort: S (small, <2ч), M (medium, полдня), L (large, день+).
4. Verification — как проверить, что шаг сделан (команда или наблюдение).
5. Если шаг ссылается на файл, которого нет — это НЕ ошибка, но такой путь попадёт в warnings.

Ответь ОДНОЙ строкой JSON (compact) строго по схеме:
{{"title":"...","summary":"...","steps":[{{"id":"S1","title":"...","description":"...","files":["..."],"effort":"S|M|L","depends_on":[],"verification":"..."}}],"risks":["..."],"warnings":["..."]}}

Ничего кроме JSON не пиши.
"""

_EFFORT_OK = {"S", "M", "L"}


def run(inputs, env):
    project_map = (inputs or {}).get("project_map")
    goal = str(env.get("oracle_goal", "")).strip()

    if not project_map or not isinstance(project_map, dict):
        return {"ok": False, "error": "project_map missing or failed"}
    if not goal:
        return {"ok": False, "error": "oracle_goal is empty"}

    llm = env.get("llm")
    if not callable(llm):
        plan = _stub_plan(goal, project_map)
        plan["brain"] = "stub"
        return {"ok": True, "plan": plan}

    prompt = _prompt(goal, project_map)
    raw = llm(prompt)
    plan = _parse(raw)
    if plan is None:
        plan = _stub_plan(goal, project_map)
        plan["brain"] = "stub"
        return {"ok": True, "plan": plan}

    plan = _validate(plan, project_map)
    plan["brain"] = "llm"
    return {"ok": True, "plan": plan}


def _prompt(goal, project_map):
    files = _prioritize_files(project_map.get("files", []), project_map.get("entrypoints", []), str(project_map.get("oracle_goal", "")))
    markers = project_map.get("markers", {})
    readme = project_map.get("readme_summary")
    inbox = project_map.get("inbox_state")
    return PROMPT_TMPL.format(
        goal=goal,
        name=project_map.get("name", "project"),
        root=project_map.get("root", ""),
        readme_summary=readme if readme else "(нет README или он пуст)",
        inbox_state=json.dumps(inbox, ensure_ascii=False) if inbox else "(нет инбокса)",
        files=", ".join(files[:200]),
        entrypoints=", ".join(project_map.get("entrypoints", [])),
        markers=json.dumps(markers, ensure_ascii=False) if markers else "(нет)",
        extensions=json.dumps(project_map.get("extensions", {}), ensure_ascii=False),
    )


def _prioritize_files(files, entrypoints, goal):
    """Поднять entrypoints и файлы, релевантные цели, в начало списка.

    LLM видит только первые 200 файлов; если важные файлы утонут в середине,
    план ссылается на несуществующие README или упускает нужные модули.
    """
    goal_tokens = set(re.findall(r"\w+", goal.lower()))
    score = {}
    for f in files:
        s = 0
        if f in entrypoints:
            s += 100
        if "test" not in f and "__pycache__" not in f:
            f_lower = f.lower()
            for tok in goal_tokens:
                if tok in f_lower:
                    s += 10
            for key in ("main", "app", "serve", "index", "run", "config", "core"):
                if key in f_lower:
                    s += 3
        score[f] = s
    return sorted(files, key=lambda f: (-score.get(f, 0), f))


def _parse(raw):
    if not raw:
        return None
    text = raw.strip()
    # Попытка вытащить JSON из markdown-обёртки.
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if m:
            text = m.group(1).strip()
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            return json.loads(ln)
        except Exception:
            continue
    return None


def _validate(plan, project_map):
    files = set(project_map.get("files", []))
    warnings = list(plan.get("warnings") or [])
    steps = []
    for step in plan.get("steps", []):
        if not isinstance(step, dict):
            continue
        step_files = []
        for f in step.get("files", []):
            if f in files:
                step_files.append(f)
            else:
                warnings.append(f"шаг {step.get('id')} ссылается на несуществующий файл: {f}")
        step["files"] = step_files
        effort = str(step.get("effort", "M")).upper()
        if effort not in _EFFORT_OK:
            effort = "M"
        step["effort"] = effort
        depends = [d for d in step.get("depends_on", []) if isinstance(d, str)]
        step["depends_on"] = depends
        steps.append(step)

    out = {
        "title": str(plan.get("title", "") or "План"),
        "summary": str(plan.get("summary", "") or ""),
        "steps": steps,
        "risks": [r for r in plan.get("risks", []) if isinstance(r, str)],
        "warnings": warnings,
    }
    return out


def _stub_plan(goal, project_map):
    name = project_map.get("name", "project")
    entrypoints = project_map.get("entrypoints", [])
    files = project_map.get("files", [])
    first_file = entrypoints[0] if entrypoints else (files[0] if files else "README.md")
    return {
        "title": f"План: {goal} ({name})",
        "summary": "Stub-план без LLM. Уточни цель или подключи ключ.",
        "steps": [
            {
                "id": "S1",
                "title": "Изучить проект",
                "description": f"Открыть {first_file} и понять текущую архитектуру.",
                "files": [first_file],
                "effort": "S",
                "depends_on": [],
                "verification": f"Прочитать {first_file}",
            },
            {
                "id": "S2",
                "title": "Сделать минимальную задачу",
                "description": f"Реализовать часть '{goal}' в {first_file}.",
                "files": [first_file],
                "effort": "M",
                "depends_on": ["S1"],
                "verification": "Запустить smoke-test",
            },
        ],
        "risks": ["Без LLM план может быть слишком общим"],
        "warnings": [],
        "brain": "stub",
    }
