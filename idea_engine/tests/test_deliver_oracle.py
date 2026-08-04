"""Тесты deliver_oracle: сохранение плана, index, inbox-карточка."""

import os
import sys
import tempfile
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from organs import deliver_oracle  # noqa: E402

PLAN = {
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
    "brain": "llm",
}


def test_deliver_creates_plan_and_index():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = os.path.join(tmp, "data")
        os.makedirs(data_dir)
        orig_data = deliver_oracle.DATA_DIR
        orig_inbox = deliver_oracle.INBOX_PATH
        orig_index = deliver_oracle.INDEX_PATH
        deliver_oracle.DATA_DIR = Path(data_dir)
        deliver_oracle.INBOX_PATH = Path(data_dir) / "inbox.md"
        deliver_oracle.INDEX_PATH = Path(data_dir) / "oracles" / "index.md"
        try:
            out = deliver_oracle.run(
                {"plan": PLAN},
                {"oracle_project": "M:/projects/note-bot", "oracle_goal": "add Telegram auth"},
            )
            assert out["ok"] is True
            assert out["slug"] == "note-bot"
            assert os.path.exists(out["plan_path"])
            assert os.path.exists(out["index_path"])
            text = open(out["plan_path"], encoding="utf-8").read()
            assert "Auth plan" in text
            assert "add Telegram auth" in text
            assert "S1 — Model" in text
        finally:
            deliver_oracle.DATA_DIR = orig_data
            deliver_oracle.INBOX_PATH = orig_inbox
            deliver_oracle.INDEX_PATH = orig_index


def test_deliver_missing_plan():
    out = deliver_oracle.run({}, {})
    assert out["ok"] is False


def test_deliver_dedups_same_goal_within_window():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = os.path.join(tmp, "data")
        os.makedirs(data_dir)
        orig_data = deliver_oracle.DATA_DIR
        orig_inbox = deliver_oracle.INBOX_PATH
        orig_index = deliver_oracle.INDEX_PATH
        orig_oracles = deliver_oracle.ORACLES_DIR
        deliver_oracle.DATA_DIR = Path(data_dir)
        deliver_oracle.INBOX_PATH = Path(data_dir) / "inbox.md"
        deliver_oracle.INDEX_PATH = Path(data_dir) / "oracles" / "index.md"
        deliver_oracle.ORACLES_DIR = Path(data_dir) / "oracles"
        try:
            out1 = deliver_oracle.run(
                {"plan": PLAN},
                {"oracle_project": "M:/projects/note-bot", "oracle_goal": "add Telegram auth"},
            )
            plan1 = out1["plan_path"]
            out2 = deliver_oracle.run(
                {"plan": PLAN},
                {"oracle_project": "M:/projects/note-bot", "oracle_goal": "add Telegram auth"},
            )
            assert out2["replaced"] is True
            assert out2["plan_path"] == plan1
            # новых файлов не появилось
            plan_dir = Path(plan1).parent
            assert len(list(plan_dir.glob("*.md"))) == 1
        finally:
            deliver_oracle.DATA_DIR = orig_data
            deliver_oracle.INBOX_PATH = orig_inbox
            deliver_oracle.INDEX_PATH = orig_index
            deliver_oracle.ORACLES_DIR = orig_oracles
