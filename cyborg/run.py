"""CLI беты киборга:  python run.py "<цель>"

Собирает исполняемые органы, гоняет оркестратор, печатает трассу и результат.
Без цели — дефолт «приноси свежие идеи» (главная работа киборга).
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # консоль Windows бывает cp1251 — принудительно utf-8, чтобы юникод не ронял вывод
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from wiring import build_organs  # noqa: E402
from orchestrator import Cyborg  # noqa: E402
from registry import load_catalog  # noqa: E402
import ask_llm  # noqa: E402
import harvest  # noqa: E402  (_source_env + wire_council: единый источник и впайка совета — как у автосбора)
from organs_vendored import scrub_secrets  # noqa: E402  (лог тоже вычищаем — не полагаемся на граф)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _council_note(out):
    """Одна честная строка про совещание на отборе: проснулся ли оркестр и кто голосовал.
    Пусто, если отбор судил не совет (нет ключей -> обычный один судья)."""
    c = out.get("council")
    if not isinstance(c, dict):
        return ""
    live = c.get("live") or []
    who = "+".join(str(x) for x in live) if live else "—"
    woke = "оркестр ПРОСНУЛСЯ" if c.get("woken") else "оркестр спал"
    return f"{woke} · голоса: {who}"


def _log_run(out):
    """Читаемый след прогона — чтобы юзер утром видел, что киборг делал."""
    os.makedirs(DATA, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    steps = " -> ".join(t.get("organ") for t in out["trace"] if t.get("organ")) or "—"
    r = out.get("result")
    rv = (str(r)[:120] if r is not None else "нет")
    line = f"- [{ts}] «{out['goal']}» → {steps} | {out['deliverable']}={rv}"
    note = _council_note(out)
    if note:
        line += f" | совет: {note}"
    line += "\n"
    # защита класса: даже если в результат/цель просочился секрет — в лог он не ляжет
    with open(os.path.join(DATA, "runs.md"), "a", encoding="utf-8") as f:
        f.write(scrub_secrets.scrub_text(line))


def main(argv):
    goal = argv[0] if argv else "приноси свежие идеи"
    try:
        cat_n = len(load_catalog())
    except Exception as e:
        cat_n = "?(" + str(e)[:30] + ")"
    cy = Cyborg(build_organs(), safe_mode=True, k=6)  # k>=6: роутер сурфейсит всю цепь (+readability_gate)
    # ЕДИНЫЙ источник: те же каналы/настройки, что у автосбора (harvest._source_env), БЕЗ
    # фильтра «уже видели» — ручной клик приносит что нашёл сейчас. Раньше env был пуст → collect
    # молча падал на дефолт HN(n=8), и «Принеси идеи» ходила мимо телеграм-пула. Это чинит ту дырку.
    env = harvest._source_env()
    if env.get("content_llm"):
        # генератор идей идёт по ТОЙ ЖЕ цепочке, что интуиция (один провайдер/ключ closerouter)
        brain_mode = f"идеи+интуиция={ask_llm._MODEL} (одна цепочка), планировщик=stub"
    else:
        brain_mode = "идеи=stub, планировщик=stub (ключа цепочки нет)"
    # СОВЕТ на шаге отбора идей (гейт снят юзером 2026-07-13): цепочка интуиции + 7-модельный
    # оркестр из ключей киборга -> отбор судит взвешенный совет, а не один судья. Впайка — через
    # harvest.wire_council: ЕДИНЫЙ источник истины, тот же провод, что у автосбора (раньше блок
    # жил только тут → фон судил одним арбитром; теперь оба пути зовут одну функцию, не разойдутся).
    # Заглушить оркестр: KIBORG_SLEEP_ORCHESTRA=1. Нет ключей -> совет спит, отбор как раньше.
    harvest.wire_council(env)
    # честно: пишем ДОСТУПНОСТЬ голосов, не факт голосования — кто в моменте ответит, тот и судит
    # (интуиция может воздержаться на сбое сети; реальные голоса прогона — в метаданных council).
    chain = env.get("llm_chain")
    if chain:
        avail = f"интуиция×{len(chain)}"
        if env.get("orchestra"):
            avail += f"+оркестр×{len(env['orchestra']['models'])}"
        brain_mode += f" | отбор: совет вкл (доступно: {avail}; кто ответит — тот голосует)"
    else:
        brain_mode += " | отбор: один судья"
    out = cy.run(goal, env=env)

    print(f"КАТАЛОГ: {cat_n} органов | ИСПОЛНЯЕМЫХ подключено: {len(cy.organs)} | {brain_mode}")
    print(f"ЦЕЛЬ: {out['goal']}  ->  нужен результат-ключ: {out['deliverable']}")
    print(f"РОУТЕР отобрал: {out['routed']}")
    print("ТРАССА ЦИКЛА:")
    for t in out["trace"]:
        line = f"  шаг {t.get('step')}: "
        if t.get("action") == "finish":
            line += "ФИНИШ — " + t.get("why", "")
        else:
            line += f"{t.get('organ')} ({t.get('why')}) -> {t.get('got')}"
            if t.get("error"):
                line += " ERROR:" + t["error"]
            if t.get("skipped"):
                line += " SKIP:" + t["skipped"]
        print(line)
    r = out["result"]
    print("РЕЗУЛЬТАТ:", (str(r)[:900] if r is not None else "(нет)"))
    note = _council_note(out)
    if note:
        print("СОВЕТ НА ОТБОРЕ:", note)
    _log_run(out)
    print("след прогона ->", os.path.join(DATA, "runs.md"))


if __name__ == "__main__":
    main(sys.argv[1:])
