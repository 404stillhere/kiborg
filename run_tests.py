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
Код возврата: 0 — все зелёные И реально прогнаны; 1 — падение/ошибка ЛИБО pytest не
выполнился / 0 собранных тестов (для CI/pre-commit; ловит «нет модуля pytest» и пустой прогон).
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

# Импорт config после bootstrap: run_tests.py лежит в корне проекта, cyborg/ — рядом.
sys.path.insert(0, os.path.join(BASE, "cyborg"))
import config  # noqa: E402

PACKAGES = config.TEST_RUNNER_PACKAGES

# «N passed», «N failed», «N error(s)» из хвоста вывода pytest -q.
_PASS = re.compile(config.TEST_RUNNER_PASSED_RE)
_FAIL = re.compile(config.TEST_RUNNER_FAILED_RE)
_ERR = re.compile(config.TEST_RUNNER_ERROR_RE)


def _count(pat, text):
    m = pat.search(text)
    return int(m.group(1)) if m else 0


def run_package(pkg):
    tests_dir = os.path.join(BASE, pkg, "tests")
    if not os.path.isdir(tests_dir):
        return {"pkg": pkg, "passed": 0, "failed": 0, "errors": 0, "skipped": True}
    try:
        # Таймаут (council 2026-08-17): зависший тест раньше вешал весь прогон навсегда;
        # теперь пакет убивается по потолку и репортится как ошибка с хвостом вывода.
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", tests_dir, "-q"],
            capture_output=True,
            text=True,
            cwd=BASE,
            timeout=config.TEST_RUNNER_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        def _s(x):
            return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")

        tail = "\n".join((_s(exc.stdout) + _s(exc.stderr)).strip().splitlines()[-config.TEST_RUNNER_TAIL_LINES:])
        return {
            "pkg": pkg,
            "passed": 0,
            "failed": 0,
            "errors": 1,
            "skipped": False,
            "rc": "timeout",
            "tail": f"ТАЙМАУТ {config.TEST_RUNNER_TIMEOUT_SEC}с — пакет убит (зависший тест?)\n{tail}",
        }
    out = proc.stdout + proc.stderr
    res = {
        "pkg": pkg,
        "passed": _count(_PASS, out),
        "failed": _count(_FAIL, out),
        "errors": _count(_ERR, out),
        "skipped": False,
        "rc": proc.returncode,
    }
    if res["failed"] or res["errors"] or proc.returncode != 0 or res["passed"] == 0:
        # печатаем сырой хвост при ЛЮБОЙ аномалии: падения/ошибки, ненулевой rc (вкл. rc=1
        # «нет модуля pytest» и rc=5 «нет собранных тестов»), либо 0 прогнанных тестов
        res["tail"] = "\n".join(out.strip().splitlines()[-config.TEST_RUNNER_TAIL_LINES :])
    return res


def _package_bad(r):
    """Пакет ПРОБЛЕМНЫЙ, если: тесты падали/ошибались, ИЛИ pytest отработал не штатно
    (rc!=0 — сюда попадают «нет модуля pytest» rc=1 и «ничего не собрано» rc=5), ИЛИ
    прогнал 0 тестов при живом пакете (passed==0 = pytest не выполнился/пусто).

    Именно это ловит ложно-зелёное: без pytest в интерпретаторе rc=1, счётчиков «N passed/
    failed» в выводе нет -> passed=failed=errors=0, и старый расчёт «bad=failed+errors» давал
    0 -> «ВСЕ ЗЕЛЁНЫЕ» + exit 0 на 0 прогнанных тестах; CI/pre-commit по exit-коду это
    пропускал. Пропущенный пакет (нет tests/) проблемным НЕ считается."""
    if r["skipped"]:
        return False
    if r["failed"] or r["errors"]:
        return True
    if r.get("rc", 0) != 0:
        return True
    return r["passed"] == 0


def main(argv):
    pkgs = [p for p in argv if p in PACKAGES] or PACKAGES
    results = [run_package(p) for p in pkgs]

    total_pass = total_fail = total_err = 0
    any_bad = False
    print("\nkiborg — тесты по пакетам (каждый свой процесс):\n")
    for r in results:
        if r["skipped"]:
            print(f"  {r['pkg']:<12} — нет tests/ (пропуск)")
            continue
        total_pass += r["passed"]
        total_fail += r["failed"]
        total_err += r["errors"]
        bad_pkg = _package_bad(r)
        any_bad = any_bad or bad_pkg
        if not bad_pkg:
            mark = config.TEST_RUNNER_STATUS_OK.ljust(5)
        elif r["failed"] or r["errors"]:
            mark = config.TEST_RUNNER_STATUS_FAIL.ljust(5)
        else:
            mark = config.TEST_RUNNER_STATUS_NORUN.ljust(5)  # pytest не выполнился / 0 тестов / rc!=0 — НЕ зелёное
        line = (
            f"  [{mark}] {r['pkg']:<12} passed={r['passed']} failed={r['failed']} "
            f"errors={r['errors']} rc={r.get('rc', '?')}"
        )
        print(line)
        if "tail" in r:
            print("        --- хвост pytest ---")
            for tl in r["tail"].splitlines():
                print(f"        {tl}")

    verdict = config.TEST_RUNNER_VERDICT_ALL_GOOD if not any_bad else config.TEST_RUNNER_VERDICT_PROBLEMS
    print(f"\nИТОГО: passed={total_pass} failed={total_fail} errors={total_err} -> {verdict}\n")
    return 1 if any_bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
