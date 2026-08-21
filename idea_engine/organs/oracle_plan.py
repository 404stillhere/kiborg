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
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from cyborg import config
from cyborg.config import PANEL_HOST, PANEL_PORT

# fmt: off
PROMPT_TMPL = """Ты технический планировщик. Построй пошаговый план от текущего состояния проекта к цели.

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

Контекст kiborg:
- Пульт (panel) — локальный веб-UI на http://{panel_host}:{panel_port}, эндпоинт статуса /api/state.
- Код пульта: panel/serve.py (сервер), panel/v2/app.js (логика), panel/v2/index.html (разметка).
- Ядро агента: cyborg/ask_llm.py, cyborg/brain.py, cyborg/mind.py, cyborg/harvest.py.
- Данные хранятся в cyborg/data/ и idea_engine/data/, но ЭТО НЕ файлы для редактирования в плане.

Требования к плану:
1. title — кратко, по существу цели.
2. summary — 1-2 предложения: что сделаем и зачем.
3. steps — от 2 до 7 шагов. id="S1", "S2"... title ≤ 10 слов, description — конкретные действия.
4. files — только файлы из списка "Файли". НЕ придумывай пути. НЕ указывай README.md как рабочий файл, если задача не в редактировании документации. НЕ указывай файлы данных (*.json в data/) как цели правок.
5. effort — S (<2ч), M (2-6ч), L (день+). Клиентская пагинация, добавление поля в JSON-ответ, правка рендера UI — S. Новый модуль/интеграция — M. Архитектурный рефакторинг — L.
6. depends_on — id предыдущих шагов, без циклов.
7. verification — конкретная команда или наблюдение. Для API пульта используй порт {panel_port}: curl -s http://{panel_host}:{panel_port}/api/state | grep ... . Для тестов: pytest <file>::<test>. Для UI: открыть http://{panel_host}:{panel_port} и увидеть изменение.
8. risks — реальные риски, связанные с проектом (не общие).
9. warnings — если цель требует файла, которого нет в карте. Если все файлы есть — оставь пустым.

Ответь ОДНОЙ строкой JSON compact. Пример:
{{"title":"Добавить авторизацию","summary":"Встроить JWT-аутентификацию в API и пульт.","steps":[{{"id":"S1","title":"Изучить точки входа","description":"Прочитать panel/serve.py и panel/v2/app.js, найти где отдаётся /api/state.","files":["panel/serve.py","panel/v2/app.js"],"effort":"S","depends_on":[],"verification":"curl -s http://{panel_host}:{panel_port}/api/state | grep -i auth"}}],"risks":["Секреты могут попасть в логи"],"warnings":[]}}

Ничего кроме JSON не пиши.
"""
# fmt: on

_EFFORT_OK = {"S", "M", "L"}

# Минимальная осмысленность цели: ≥N слов, не из запрещённого списка.
_MIN_GOAL_WORDS = config.ORACLE_PLAN_MIN_GOAL_WORDS
_VAGUE_GOALS = {"test", "tests", "testing", "fix", "bug", "todo", "todo list", "plan", "oracle", "run"}


def _validate_goal(goal):
    words = goal.split()
    if len(words) < _MIN_GOAL_WORDS:
        return f"цель слишком короткая: опиши что сделать минимум {_MIN_GOAL_WORDS} словами"
    lowered = goal.lower().strip(" .!?:")
    if lowered in _VAGUE_GOALS:
        return f'цель слишком абстрактная: "{goal}" — уточни, что именно нужно сделать'
    return None


def run(inputs, env):
    project_map = (inputs or {}).get("project_map")
    goal = str(env.get("oracle_goal", "")).strip()

    if not project_map or not isinstance(project_map, dict):
        return {"ok": False, "error": "project_map missing or failed"}
    if not goal:
        return {"ok": False, "error": "oracle_goal is empty"}
    bad = _validate_goal(goal)
    if bad:
        return {"ok": False, "error": bad}

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
    files = _prioritize_files(
        project_map.get("files", []), project_map.get("entrypoints", []), str(project_map.get("oracle_goal", ""))
    )
    markers = project_map.get("markers", {})
    readme = project_map.get("readme_summary")
    inbox = project_map.get("inbox_state")
    return PROMPT_TMPL.format(
        goal=goal,
        name=project_map.get("name", "project"),
        root=project_map.get("root", ""),
        readme_summary=readme if readme else "(нет README или он пуст)",
        inbox_state=json.dumps(inbox, ensure_ascii=False) if inbox else "(нет инбокса)",
        files=", ".join(files[: config.ORACLE_PLAN_MAX_FILES]),
        entrypoints=", ".join(project_map.get("entrypoints", [])),
        markers=json.dumps(markers, ensure_ascii=False) if markers else "(нет)",
        extensions=json.dumps(project_map.get("extensions", {}), ensure_ascii=False),
        panel_port=PANEL_PORT,
        panel_host=PANEL_HOST,
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
            s += config.ORACLE_PLAN_ENTRYPOINT_SCORE
        if "test" not in f and "__pycache__" not in f:
            f_lower = f.lower()
            for tok in goal_tokens:
                if tok in f_lower:
                    s += config.ORACLE_PLAN_KEYWORD_SCORE
            for key in ("main", "app", "serve", "index", "run", "config", "core"):
                if key in f_lower:
                    s += config.ORACLE_PLAN_NAME_SCORE
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
