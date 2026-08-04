"""Тесты oracle_scan: разрешение путей, дерево, маркеры, entrypoints."""

import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from organs import oracle_scan  # noqa: E402


def _make_project():
    d = tempfile.mkdtemp(prefix="oracle_test_")
    root = os.path.join(d, "note-bot")
    os.makedirs(root)
    os.makedirs(os.path.join(root, "src"))
    with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as f:
        f.write("# Note bot\n")
    with open(os.path.join(root, "main.py"), "w", encoding="utf-8") as f:
        f.write("# TODO: add auth\nprint('hi')\n")
    with open(os.path.join(root, "src", "db.py"), "w", encoding="utf-8") as f:
        f.write("# FIXME: use migrations\n")
    return root


def test_scan_full_map():
    root = _make_project()
    out = oracle_scan.run({}, {"oracle_project": root})
    assert out["ok"] is True
    mp = out["project_map"]
    assert mp["name"] == "note-bot"
    assert mp["root"] == root
    assert set(mp["files"]) == {"README.md", "main.py", "src/db.py"}
    assert mp["entrypoints"] == ["README.md", "main.py"]
    assert mp["markers"].get("main.py") == 1
    assert mp["markers"].get("src/db.py") == 1
    tree = "\n".join(mp["tree"])
    assert "note-bot/" in tree
    assert "main.py" in tree
    assert "db.py" in tree


def test_missing_project():
    out = oracle_scan.run({}, {"oracle_project": "/nonexistent/path/oracle_xyz"})
    assert out["ok"] is False
    assert "not found" in out["error"]


def test_relative_path_with_root():
    root = _make_project()
    base = os.path.dirname(root)
    name = os.path.basename(root)
    out = oracle_scan.run({}, {"oracle_project": name, "projects_root": base})
    assert out["ok"] is True
    assert out["project_map"]["name"] == "note-bot"


def test_entrypoints_sorted_and_unique():
    root = _make_project()
    out = oracle_scan.run({}, {"oracle_project": root})
    ep = out["project_map"]["entrypoints"]
    assert ep == sorted(set(ep))
    assert "README.md" in ep
    assert "main.py" in ep


def test_extensions_histogram():
    root = _make_project()
    out = oracle_scan.run({}, {"oracle_project": root})
    ext = out["project_map"]["extensions"]
    assert ext.get(".py") == 2
    assert ext.get(".md") == 1
