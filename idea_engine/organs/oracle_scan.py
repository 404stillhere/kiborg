"""Орган Oracle: карта проекта из файловой системы. Без LLM, без сети.

Контракт: run(inputs, env) -> {"project_map": {...}}.
Входы через env:
  oracle_project  — абсолютный или относительный путь к проекту.
  projects_root   — база для относительных путей (default M:/projects).

Выход project_map:
  root, name, file_count, tree, files, extensions, markers, entrypoints,
  errors, truncated.
"""

import os
import re
from collections import Counter
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    "out",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "coverage",
}

MAX_FILES = 2000
MAX_TREE_DEPTH = 4
CONTENT_MAX_BYTES = 200_000
ENTRYPOINTS = {
    "README.md",
    "readme.md",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "main.py",
    "app.py",
    "manage.py",
    "index.js",
    "index.ts",
    "docker-compose.yml",
}
MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)


def run(inputs, env):
    root = _resolve_root(env)
    if root is None:
        return {
            "ok": False,
            "error": "project dir not found",
            "suggest": "проверь oracle_project и projects_root",
        }

    files = []
    markers = {}
    entrypoints = []
    errors = []

    for dirpath, dirnames, filenames in _walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            rel = _rel(Path(dirpath) / name, root)
            files.append(rel)
            if name in ENTRYPOINTS:
                entrypoints.append(rel)
            count = _count_markers(Path(dirpath) / name, errors)
            if count:
                markers[rel] = count

    tree = _build_tree(root.name, files, depth=MAX_TREE_DEPTH)
    extensions = _ext_histogram(files)

    return {
        "ok": True,
        "project_map": {
            "root": str(root),
            "name": root.name,
            "file_count": len(files),
            "tree": tree,
            "files": files[:MAX_FILES],
            "extensions": extensions,
            "markers": dict(sorted(markers.items())),
            "entrypoints": entrypoints,
            "errors": errors[:20],
            "truncated": len(files) > MAX_FILES,
        },
    }


def _resolve_root(env):
    raw = str(env.get("oracle_project", "")).strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        base = str(env.get("projects_root", "M:/projects"))
        p = Path(base).expanduser() / p
    try:
        p = p.resolve()
    except OSError:
        return None
    return p if p.is_dir() else None


def _walk(root):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        yield Path(dirpath), dirnames, filenames


def _rel(path, root):
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _count_markers(path, errors):
    try:
        data = path.read_bytes()
        if len(data) > CONTENT_MAX_BYTES:
            data = data[:CONTENT_MAX_BYTES]
        text = data.decode("utf-8", errors="replace")
        return len(MARKER_RE.findall(text))
    except OSError:
        errors.append(f"cannot read {path}")
        return 0


def _ext_histogram(files):
    hist = Counter()
    for f in files:
        dot = f.rfind(".")
        if dot != -1:
            hist[f[dot:]] += 1
    return dict(hist)


def _build_tree(name, files, depth):
    """Плоское дерево строками: ['note-bot/', '  main.py', '  src/', '    db.py']."""
    prefix = f"{name}/"
    seen = set()
    lines = []
    for f in files:
        parts = f.split("/")
        if len(parts) > depth:
            parts = parts[:depth] + ["…"]
        for i in range(len(parts)):
            seg = "/".join(parts[: i + 1])
            if seg in seen:
                continue
            seen.add(seg)
            level = i + 1
            indent = "  " * level
            label = parts[i]
            lines.append(f"{indent}{label}")
    return [prefix] + lines
