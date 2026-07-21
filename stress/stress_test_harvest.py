#!/usr/bin/env python3
"""Стресс-тест kiborg: N прогонов harvest без LLM и сети.

Использование:
    python stress/stress_test_harvest.py [N]

По умолчанию N=50. Цель — убедиться, что 50+ прогонов не вызывают утечек памяти,
деградации производительности или накопления ошибок в логах.

Мокает:
- LLM: ask_llm.available = False → органы на стабах
- Сеть: collect_source.run → возвращает 3 фейковых items
- State/runs: все data/файлы redirect'ятся во временную директорию

Результаты:
- Среднее время прогона (мс/итерация)
- Пиковый рост памяти (KB)
- Ошибки в runs.md (должно быть 0)
"""

import os
import sys
import tempfile
import time
import tracemalloc
from contextlib import contextmanager

# sys.path bootstrap (как в фасадах)
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "cyborg"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "idea_engine"))

import bootstrap_paths

bootstrap_paths.ensure_project_paths()

import harvest  # noqa: E402
import wiring  # noqa: E402


@contextmanager
def _patched_state(tmpdir):
    """Редирект всех state/data файлов во временную директорию."""
    orig_state = harvest.STATE_FILE
    orig_ie_data = harvest._IE_DATA
    orig_data = harvest.DATA

    # paths от config (mutation для тестов)
    harvest.STATE_FILE = os.path.join(tmpdir, "harvest_state.json")
    harvest._IE_DATA = tmpdir  # inbox/state внутри tmpdir
    harvest.DATA = tmpdir  # runs.md внутри tmpdir

    # seen_items тоже редиректим (он живёт в idea_engine)
    import seen_items

    orig_seen = seen_items.PATH
    seen_items.PATH = os.path.join(tmpdir, "seen_items.json")

    try:
        yield
    finally:
        harvest.STATE_FILE = orig_state
        harvest._IE_DATA = orig_ie_data
        harvest.DATA = orig_data
        seen_items.PATH = orig_seen


@contextmanager
def _patched_collect():
    """Мокаем collect_source.run — возвращаем 3 фейковых items без сети."""
    from organs import collect_source

    orig_run = collect_source.run

    def fake_run(inputs, env):
        """Фейковый источник — 3 items, без сети, без degradation."""
        return {
            "items": [
                {"title": "stub-idea-1", "url": "http://example.com/1", "id": "1", "source": "stress"},
                {"title": "stub-idea-2", "url": "http://example.com/2", "id": "2", "source": "stress"},
                {"title": "stub-idea-3", "url": "http://example.com/3", "id": "3", "source": "stress"},
            ],
            "degraded": False,
        }

    collect_source.run = fake_run
    wiring.collect_source.run = fake_run  # wiring тоже мокаем (через _collect_locked)

    try:
        yield
    finally:
        collect_source.run = orig_run
        wiring.collect_source.run = orig_run


@contextmanager
def _patched_llm():
    """Мокаем LLM — unavailable → органы на стабах."""
    orig_available = harvest.ask_llm.available

    harvest.ask_llm.available = lambda: False

    try:
        yield
    finally:
        harvest.ask_llm.available = orig_available


def _count_runs_md_errors(tmpdir):
    """Считаем строки с ошибками в runs.md."""
    runs_path = os.path.join(tmpdir, "runs.md")
    if not os.path.exists(runs_path):
        return 0
    errors = 0
    with open(runs_path, encoding="utf-8") as f:
        for line in f:
            # Признаки ошибки: слово "ошибка", "error", "Exception", "Traceback" (полагаем на русском логе)
            if any(word in line for word in ["ошибка", "error", "Exception", "Traceback", "Failed", "failed"]):
                errors += 1
    return errors


def main(n=50):
    """Запуск N прогонов harvest с замерами времени/памяти."""
    print(f"=== Stress test: {n} прогонов harvest ===")
    print("Моки: LLM=unavailable, collect_source=3 stub items, files→tmpdir")

    tmpdir = tempfile.mkdtemp(prefix="stress_")
    print(f"Временная директория: {tmpdir}")

    tracemalloc.start()
    peak_mem = 0
    times = []

    with _patched_state(tmpdir), _patched_collect(), _patched_llm():
        # Строим organs один раз (не на каждой итерации)
        organs = harvest.build_organs()
        cy = harvest.Cyborg(organs, safe_mode=True, k=6)

        goal = "стресс-тест"
        env = harvest._source_env()  # env без LLM (available=False)

        for i in range(n):
            start = time.perf_counter()
            out = cy.run(goal, env=env)  # конвейер: collect→ideate→rank→scrub→deliver
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)

            # Текущий пиковый мем
            current, mem_peak = tracemalloc.get_traced_memory()
            peak_mem = max(peak_mem, mem_peak)

            # Прогресс каждые 10 прогонов
            if (i + 1) % 10 == 0:
                print(f"Прогон {i+1}/{n}: {elapsed_ms:.1f}ms, peak_mem={peak_mem/1024:.1f}KB")

    # Финальные метрики
    mean_ms = sum(times) / len(times) if times else 0
    runs_md_errors = _count_runs_md_errors(tmpdir)

    print(f"\n=== Результаты ({n} прогонов) ===")
    print(f"Среднее время: {mean_ms:.1f}ms/итерация")
    print(f"Пиковый рост памяти: {peak_mem/1024:.1f}KB")
    print(f"Ошибок в runs.md: {runs_md_errors}")

    if runs_md_errors > 0:
        print(f"[WARN] runs.md содержит {runs_md_errors} строк с ошибками!")
    else:
        print("[OK] runs.md чист")

    # Очистка
    import shutil

    shutil.rmtree(tmpdir)
    tracemalloc.stop()

    # Выходной код (для CI)
    return 1 if runs_md_errors > 0 else 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Стресс-тест kiborg: N прогонов harvest")
    parser.add_argument("n", nargs="?", type=int, default=50, help="Число прогонов (по умолчанию 50)")
    args = parser.parse_args()

    n = max(1, min(args.n, 1000))  # ceiling 1000
    sys.exit(main(n))
