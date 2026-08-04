"""CLI Oracle-режим: run.py --mode oracle --project ... --goal ..."""

import os
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(args):
    env = os.environ.copy()
    env["PYTHONPATH"] = BASE
    return subprocess.run(
        [sys.executable, os.path.join(BASE, "run.py"), "--mode", "oracle"] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=BASE,
    )


def test_oracle_cli_success():
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "demo")
        os.makedirs(proj)
        with open(os.path.join(proj, "main.py"), "w", encoding="utf-8") as f:
            f.write("print('hello')\n")
        r = _run(["--project", proj, "--goal", "add auth", "--root", tmp])
        assert r.returncode == 0, r.stderr
        assert "План сохранён" in r.stdout, r.stdout
        assert "oracle_scan" in r.stdout
        assert "oracle_plan" in r.stdout
        assert "deliver_oracle" in r.stdout


def test_oracle_cli_missing_project():
    r = _run(["--project", "M:/projects/nonexistent_oracle_test", "--goal", "x", "--root", "M:/projects"])
    # oracle_scan сообщает об ошибке; проверяем, что план не сохранён
    assert "Oracle не удался" in r.stdout
    assert "План сохранён" not in r.stdout
