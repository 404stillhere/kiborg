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


def _log_run(out):
    """Читаемый след прогона — чтобы юзер утром видел, что киборг делал."""
    os.makedirs(DATA, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    steps = " -> ".join(t.get("organ") for t in out["trace"] if t.get("organ")) or "—"
    r = out.get("result")
    rv = (str(r)[:120] if r is not None else "нет")
    line = f"- [{ts}] «{out['goal']}» → {steps} | {out['deliverable']}={rv}"
    note = harvest.council_note(out)
    if note:
        line += f" | совет: {note}"
    dn = harvest._degrade_note(out)
    if dn:
        line += f" | ⚠ {dn}"          # деградация видна в истории (обе кнопки согласованы)
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
        # генератор идей идёт по ТОЙ ЖЕ цепочке, что интуиция (гибрид gemini→muse, см. keychain._SPEC)
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
    # ЖИВОЙ прогресс: конвейер с реальными моделями идёт минуты (deepseek — рассуждающая, ~5с/вызов;
    # совет × идея). Без этого пульт/консоль молчат весь прогон и кажутся зависшими (жалоба юзера
    # 2026-07-15). Печатаем текущий орган с flush — пульт стримит stdout построчно.
    _PHASE = {"start": "⏳ иду", "done": "✓ готов", "finish": "🏁 финиш"}
    def _on_step(step, phase, name, why):
        tag = _PHASE.get(phase, phase)
        tail = f" — {why}" if why else ""
        print(f"  {tag}: {name}{tail}" if name else f"  {tag}{tail}", flush=True)
    # суб-прогресс ВНУТРИ медленного органа (readability/ideate шлют «переписываю i/N») — env-контракт,
    # орган зовёт env["on_progress"] если он есть. Даёт живую строку, пока один орган молотит минуты.
    env["on_progress"] = lambda msg: print(f"     · {msg}", flush=True)
    print("иду по конвейеру (живые модели думают, это может занять пару минут)…", flush=True)
    out = cy.run(goal, env=env, on_step=_on_step)

    if env.get("direction"):
        print(f"НАПРАВЛЕНИЕ: идеи в сторону «{env['direction']}»")
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
    note = harvest.council_note(out)
    if note:
        print("СОВЕТ НА ОТБОРЕ:", note)
    dn = harvest._degrade_note(out)
    if dn:
        print("⚠ ДЕГРАДАЦИЯ:", dn)
    # Человеку понятно про 0 идей: пустой результат + отсеянные болванки = генератор был занят
    # (rate-limit / пустой ответ), НЕ поломка. Иначе юзер видит «РЕЗУЛЬТАТ 0 · stub-отсеяно=5» и
    # думает «сломалось» (жалоба 2026-07-15). Ключи целы — это транзиент, повтори прогон.
    if out.get("dropped_stub") and not out.get("result"):
        print("💡 ВНИМАНИЕ: ключи есть, но LLM упал на 0 (нулевой баланс, обрыв сети). "
              "Идеи доставлены как болванки.", flush=True)
    if out.get("dropped_dup"):
        print(f"♻️ Отклонено дубликатов: {out['dropped_dup']} (идеи уже были в пуле)", flush=True)
    _log_run(out)
    print("след прогона ->", os.path.join(DATA, "runs.md"))


if __name__ == "__main__":
    main(sys.argv[1:])
