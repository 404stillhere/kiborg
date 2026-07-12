"""Единый прогон всех тестов kiborg — по пакетам, КАЖДЫЙ в своём процессе.

Зачем отдельный раннер, а не голый `pytest` из корня:
  cyborg/ и idea_engine/ содержат ОДНОИМЁННЫЕ модули (`run.py`, `store.py`, …). Тесты
  каждого пакета кладут свою папку в sys.path и делают `import run`. При ЕДИНОМ прогоне
  pytest первый импортированный `run` кэшируется в sys.modules, и тесты второго пакета
  получают ЧУЖОЙ модуль -> ложные падения вида «module 'run' has no attribute
  'collect_source'». Это НЕ баг кода: по пакетам-раздельно все зелёные.
  Раздельные процессы дают каждому пакету свежий sys.modules -> коллизии нет.

Запуск:  python run_tests.py            (все пакеты)
         python run_tests.py cyborg    (только указанные)
Код возврата: 0 — все зелёные; 1 — где-то падение/ошибка (для CI/pre-commit).
"""
import os
import re
import subprocess
import sys

try:  # консоль Windows по умолчанию cp1251 — кириллица/символы иначе роняют print
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
PACKAGES = ["cyborg", "idea_engine", "panel"]

# «N passed», «N failed», «N error(s)» из хвоста вывода pytest -q.
_PASS = re.compile(r"(\d+) passed")
_FAIL = re.compile(r"(\d+) failed")
_ERR = re.compile(r"(\d+) error")


def _count(pat, text):
    m = pat.search(text)
    return int(m.group(1)) if m else 0


def run_package(pkg):
    tests_dir = os.path.join(BASE, pkg, "tests")
    if not os.path.isdir(tests_dir):
        return {"pkg": pkg, "passed": 0, "failed": 0, "errors": 0, "skipped": True}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", tests_dir, "-q"],
        capture_output=True, text=True, cwd=BASE,
    )
    out = proc.stdout + proc.stderr
    res = {
        "pkg": pkg,
        "passed": _count(_PASS, out),
        "failed": _count(_FAIL, out),
        "errors": _count(_ERR, out),
        "skipped": False,
        "rc": proc.returncode,
    }
    if res["failed"] or res["errors"] or proc.returncode not in (0, 5):
        # 5 = pytest «нет собранных тестов»; печатаем сырой хвост для диагностики
        res["tail"] = "\n".join(out.strip().splitlines()[-15:])
    return res


def main(argv):
    pkgs = [p for p in argv if p in PACKAGES] or PACKAGES
    results = [run_package(p) for p in pkgs]

    total_pass = total_fail = total_err = 0
    print("\nkiborg — тесты по пакетам (каждый свой процесс):\n")
    for r in results:
        if r["skipped"]:
            print(f"  {r['pkg']:<12} — нет tests/ (пропуск)")
            continue
        total_pass += r["passed"]
        total_fail += r["failed"]
        total_err += r["errors"]
        mark = "OK " if not (r["failed"] or r["errors"]) else "FAIL"
        line = f"  [{mark}] {r['pkg']:<12} passed={r['passed']} failed={r['failed']} errors={r['errors']}"
        print(line)
        if "tail" in r:
            print("        --- хвост pytest ---")
            for tl in r["tail"].splitlines():
                print(f"        {tl}")

    bad = total_fail + total_err
    print(f"\nИТОГО: passed={total_pass} failed={total_fail} errors={total_err} "
          f"-> {'ВСЕ ЗЕЛЁНЫЕ' if not bad else 'ЕСТЬ ПАДЕНИЯ'}\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
