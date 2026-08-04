"""Тесты oracle_plan: парсинг, валидация файлов, stub при отсутствии llm."""

import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from organs import oracle_plan  # noqa: E402

MAP = {
    "name": "note-bot",
    "root": "M:/projects/note-bot",
    "files": ["README.md", "main.py", "src/db.py"],
    "entrypoints": ["README.md", "main.py"],
    "markers": {"main.py": 1},
    "extensions": {".py": 2, ".md": 1},
}


def test_stub_when_no_llm():
    out = oracle_plan.run(
        {"project_map": MAP},
        {"oracle_goal": "add Telegram auth"},
    )
    assert out["ok"] is True
    plan = out["plan"]
    assert plan["brain"] == "stub"
    assert any("README.md" in s["files"] for s in plan["steps"])


def test_llm_json_parsed():
    raw = json.dumps(
        {
            "title": "Auth plan",
            "summary": "Add tg auth",
            "steps": [
                {
                    "id": "S1",
                    "title": "Model",
                    "description": "add user model",
                    "files": ["src/db.py"],
                    "effort": "M",
                    "depends_on": [],
                    "verification": "pytest",
                }
            ],
            "risks": ["secrets"],
            "warnings": [],
        },
        ensure_ascii=False,
    )
    out = oracle_plan.run(
        {"project_map": MAP},
        {"oracle_goal": "add Telegram auth", "llm": lambda p: raw},
    )
    assert out["ok"] is True
    plan = out["plan"]
    assert plan["brain"] == "llm"
    assert plan["title"] == "Auth plan"
    assert plan["steps"][0]["effort"] == "M"


def test_llm_wrapped_in_markdown():
    raw = (
        "```json\n"
        + json.dumps(
            {"title": "T", "summary": "S", "steps": [], "risks": [], "warnings": []},
            ensure_ascii=False,
        )
        + "\n```"
    )
    out = oracle_plan.run(
        {"project_map": MAP},
        {"oracle_goal": "x", "llm": lambda p: raw},
    )
    assert out["ok"] is True
    assert out["plan"]["brain"] == "llm"


def test_missing_file_goes_to_warnings():
    raw = json.dumps(
        {
            "title": "T",
            "summary": "S",
            "steps": [
                {
                    "id": "S1",
                    "title": "X",
                    "description": "d",
                    "files": ["src/db.py", "ghost.py"],
                    "effort": "s",
                    "depends_on": [],
                    "verification": "v",
                }
            ],
            "risks": [],
            "warnings": [],
        },
        ensure_ascii=False,
    )
    out = oracle_plan.run(
        {"project_map": MAP},
        {"oracle_goal": "x", "llm": lambda p: raw},
    )
    plan = out["plan"]
    assert "ghost.py" not in plan["steps"][0]["files"]
    assert any("ghost.py" in w for w in plan["warnings"])


def test_invalid_effort_normalized():
    raw = json.dumps(
        {
            "title": "T",
            "summary": "S",
            "steps": [
                {
                    "id": "S1",
                    "title": "X",
                    "description": "d",
                    "files": [],
                    "effort": "huge",
                    "depends_on": [],
                    "verification": "v",
                }
            ],
            "risks": [],
            "warnings": [],
        },
        ensure_ascii=False,
    )
    out = oracle_plan.run(
        {"project_map": MAP},
        {"oracle_goal": "x", "llm": lambda p: raw},
    )
    assert out["plan"]["steps"][0]["effort"] == "M"


def test_unparseable_llm_falls_to_stub():
    out = oracle_plan.run(
        {"project_map": MAP},
        {"oracle_goal": "x", "llm": lambda p: "не json"},
    )
    assert out["ok"] is True
    assert out["plan"]["brain"] == "stub"
