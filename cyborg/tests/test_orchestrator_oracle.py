"""Тесты Oracle-режима в orchestrator."""

import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from orchestrator import Cyborg  # noqa: E402


def test_oracle_mode_runs_chain():
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "demo")
        os.makedirs(proj)
        with open(os.path.join(proj, "main.py"), "w", encoding="utf-8") as f:
            f.write("print('hello')\n")

        cy = Cyborg([])
        out = cy.run(
            "oracle: add auth",
            env={
                "mode": "oracle",
                "oracle_project": proj,
                "oracle_goal": "add auth",
            },
        )
        assert out["mode"] == "oracle"
        assert out["result"]["ok"] is True
        assert out["result"]["slug"] == "demo"
        assert os.path.exists(out["result"]["plan_path"])


def test_oracle_mode_ignores_ideas_organs():
    # Даже если органы идей переданы, в oracle-режиме они не используются
    from wiring import build_organs

    cy = Cyborg(build_organs())
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "demo2")
        os.makedirs(proj)
        with open(os.path.join(proj, "main.py"), "w", encoding="utf-8") as f:
            f.write("# TODO: x\n")
        out = cy.run(
            "oracle: fix todo",
            env={
                "mode": "oracle",
                "oracle_project": proj,
                "oracle_goal": "fix todo",
            },
        )
        assert out["mode"] == "oracle"
        assert out["result"]["ok"] is True
        trace_names = [t.get("organ") for t in out["trace"]]
        assert "oracle_scan" in trace_names
        assert "oracle_plan" in trace_names
        assert "deliver_oracle" in trace_names
        # идейные органы не вызывались
        assert "collect_source" not in trace_names
        assert "ideate" not in trace_names
