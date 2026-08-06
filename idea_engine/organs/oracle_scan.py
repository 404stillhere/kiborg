"""Орган Oracle: карта проекта из файловой системы. Без LLM, без сети.

Контракт: run(inputs, env) -> {"project_map": {...}}.
Входы через env:
  oracle_project  — абсолютный или относительный путь к проекту.
  projects_root   — база для относительных путей (default M:/projects).

Выход project_map:
  root, name, file_count, tree, files, extensions, markers, entrypoints,
  readme_summary, inbox_state, errors, truncated.
"""

import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cyborg"))
import config  # noqa: E402

SKIP_DIRS = config.ORACLE_SCAN_SKIP_DIRS
SKIP_PATH_PATTERNS = {re.compile(p) for p in config.ORACLE_SCAN_SKIP_PATH_PATTERNS}
KEEP_EXTENSIONS = config.ORACLE_SCAN_KEEP_EXTENSIONS
MAX_FILES = config.ORACLE_SCAN_MAX_FILES
MAX_TREE_DEPTH = config.ORACLE_SCAN_MAX_TREE_DEPTH
CONTENT_MAX_BYTES = config.ORACLE_SCAN_CONTENT_MAX_BYTES
ENTRYPOINTS = config.ORACLE_SCAN_ENTRYPOINTS
MARKER_RE = config.ORACLE_SCAN_MARKER_RE


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
    readme_summary = _read_readme(root, errors)
    inbox_state = _read_inbox_state(root, errors)

    for dirpath, dirnames, filenames in _walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            rel = _rel(Path(dirpath) / name, root)
            if _skip_file(rel):
                continue
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
            "readme_summary": readme_summary,
            "inbox_state": inbox_state,
            "errors": errors[: config.ORACLE_SCAN_MAX_ERRORS],
            "truncated": len(files) > MAX_FILES,
        },
    }


def _resolve_root(env):
    raw = str(env.get("oracle_project", "")).strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        base = str(env.get("projects_root", config.DEFAULT_PROJECTS_ROOT))
        p = Path(base).expanduser() / p
    try:
        p = p.resolve()
    except OSError:
        return None
    return p if p.is_dir() else None


def _skip_file(rel):
    """Исключить шумовые/сгенерированные файлы из карты проекта."""
    rel_str = str(rel)
    if any(pat.search(rel_str) for pat in SKIP_PATH_PATTERNS):
        return True
    dot = rel_str.rfind(".")
    if dot == -1:
        return False
    return rel_str[dot:].lower() not in KEEP_EXTENSIONS


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
        text = data.decode(config.HTTP_CHARSET_UTF8, errors=config.HTTP_DECODE_ERRORS_REPLACE)
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


def _read_inbox_state(root, errors):
    """Краткая сводка по инбоксу: сколько идей, последние 5 заголовков."""
    inbox_path = root / "idea_engine" / "data" / "inbox.md"
    if not inbox_path.is_file():
        return None
    try:
        data = inbox_path.read_bytes()[: config.ORACLE_SCAN_CONTENT_MAX_BYTES]
        text = data.decode(config.HTTP_CHARSET_UTF8, errors=config.HTTP_DECODE_ERRORS_REPLACE)
    except OSError as e:
        errors.append(f"cannot read {inbox_path}: {e}")
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("-")]
    titles = []
    for line in lines[: config.ORACLE_SCAN_INBOX_LINES]:
        m = re.search(r"\*\*(.+?)\*\*", line)
        if m:
            titles.append(m.group(1))
    recent = titles[: config.ORACLE_SCAN_INBOX_RECENT]
    return {
        "path": str(inbox_path.relative_to(root).as_posix()),
        "total_approx": len(lines),
        "recent_titles": recent,
    }


def _read_readme(root, errors):
    """Краткое описание проекта из README: первые 20 строк после заголовка и code-блоков.

    Читается только метаданные: описание и список возможностей, не код. Лимит 2 КиБ, чтобы
    не раздувать project_map для планировщика. None если README не найден / не читается.
    """
    for cand in ("README.md", "readme.md", "README.MD", "Readme.md"):
        path = root / cand
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()[: config.ORACLE_SCAN_README_BYTES]
            text = data.decode(config.HTTP_CHARSET_UTF8, errors=config.HTTP_DECODE_ERRORS_REPLACE)
        except OSError as e:
            errors.append(f"cannot read {path}: {e}")
            return None
        return _summarize_readme(text)
    return None


def _summarize_readme(text):
    """Сжать README до описания проекта: пропустить заголовок, code-блоки, ссылки; первое
    содержательное предложение и список возможностей (если есть). Возвращает короткую строку."""
    lines = []
    in_code = False
    seen_heading = False
    skip_section = False
    buf = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if line.startswith("#"):
            seen_heading = True
            continue
        if not line:
            continue
        if line.startswith(("[!", "![", "http://", "https://", "<", "|")):
            continue
        if line.startswith("-"):
            buf.append(line)
            continue
        if not seen_heading:
            continue
        if skip_section:
            if line.startswith("#") or not line:
                skip_section = False
            else:
                continue
        if line.lower().startswith(("license", "ci", "status", "badges", "table of contents")):
            skip_section = True
            continue
        lines.append(line)
        if len(lines) >= config.ORACLE_SCAN_README_LINES:
            break
    if buf and len(lines) < 5:
        lines.extend(buf[: config.ORACLE_SCAN_README_FEATURES_BUF])
    return " ".join(lines)[: config.ORACLE_SCAN_README_SUMMARY_MAX_CHARS] if lines else None


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
