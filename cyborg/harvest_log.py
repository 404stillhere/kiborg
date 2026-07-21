"""Лог прогона и форматтеры выхлопа (council_note / _degrade_note / _log).

Вынесено из монолита harvest.py: одна зона — отрендерить человекочитаемую строку про совет
и деградацию прогона (единый форматтер для ОБЕИХ кнопок: harvest._log + run.py) и дописать
строку в data/runs.md (через scrub_secrets — секреты не утекают в лог). Константа DATA и
орган scrub_secrets читаем через фасад `import harvest`.
"""

import datetime
import os

import config


def council_note(out):
    """Одна честная строка про совещание на отборе: проснулся ли оркестр и кто голосовал.
    Пусто, если отбор судил не совет (нет ключей -> обычный один судья). ЕДИНЫЙ форматтер
    для ОБЕИХ кнопок (harvest._log + run.py) — чтобы история пульта одинаково показывала
    совет и у авто-, и у ручного прогона (раньше фон логировался БЕЗ пометки → выглядел как
    «судил один арбитр», хотя совет уже впаян)."""
    c = out.get("council")
    if not isinstance(c, dict):
        return ""
    live = c.get("live") or []
    who = "+".join(str(x) for x in live) if live else "—"
    woke = "оркестр ПРОСНУЛСЯ" if c.get("woken") else "оркестр спал"
    return f"{woke} · голоса: {who}"


def _degrade_note(out):
    """Строка про ДЕГРАДАЦИЮ прогона для консоли/лога (root #1: показать сбой, а не прятать за
    «доставлено N»). Пусто, если прогон здоров. Источник ушёл в фолбэк (4 захардкоженных
    заголовка) → «источник в фолбэке»; доставка отсеяла болванки, но живые идеи БЫЛИ → «stub-отсеяно=N»;
    модель не ответила ВОВСЕ (вся партия — болванки, инбокс пуст) → «мозг недоступен — идей нет»;
    провайдер генератора → «модель=…» ВСЕГДА (реш. юзера 2026-07-21: раньше прятали «бесплатную»
    gemini-подписку и флажили только платный фолбэк — теперь вся цепочка closerouter, делить на
    платный/бесплатный больше не на что, тег бесполезен; показываем id модели как есть — полезно
    видеть, какое плечо цепочки ответило: muse-spark=первичная, deepseek/nemotron=фолбэк);
    скраб вычистил секрет из идеи → «секретов-вырезано=N» (сигнал БЕЗОПАСНОСТИ, не деградация выдачи —
    в источник просочился секрет, surface, иначе счётчик redacted молча теряется)."""
    flags = []
    if out.get("degraded"):
        flags.append("источник в фолбэке")
    if out.get("brain_down"):
        # ключ есть, но модель молчит: вся партия — болванки, в инбокс не пущены → он честно пуст
        flags.append("мозг недоступен — идей нет")
    elif out.get("dropped_stub"):
        flags.append(f"stub-отсеяно={out['dropped_stub']}")
    if out.get("dropped_dup"):
        flags.append(f"дубликатов={out['dropped_dup']}")
    if out.get("redacted"):
        # НЕ деградация выдачи, а сигнал БЕЗОПАСНОСТИ: скраб поймал секрет в идее и вычистил его
        # перед доставкой. >0 = в источник просочился секрет — surface, иначе счётчик молча теряется.
        flags.append(f"секретов-вырезано={out['redacted']}")
    # провайдер генератора — ВСЕГДА (вся цепочка на closerouter, делить не на что; см. docstring)
    prov = out.get("provider") or ""
    if prov:
        flags.append(f"модель={prov}")
    return " · ".join(flags)


def _log(goal, out):
    import alerts
    import harvest

    os.makedirs(harvest.DATA, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    steps = " -> ".join(t.get("organ") for t in out["trace"] if t.get("organ")) or "—"
    r = out.get("result")
    rv = str(r)[:120] if r is not None else "нет"
    line = f"- [{ts}] «{goal}» → {steps} | {out['deliverable']}={rv}"
    note = council_note(out)
    if note:
        line += f" | совет: {note}"  # тот же хвост, что у ручного прогона — пульт его уже парсит
    dn = _degrade_note(out)
    if dn:
        line += f" | ⚠ {dn}"  # деградация видна в истории пульта, не только в консоли
    line += "\n"
    runs_path = os.path.join(harvest.DATA, "runs.md")
    with open(runs_path, "a", encoding="utf-8") as f:
        f.write(harvest.scrub_secrets.scrub_text(line))
    _rotate_if_needed(runs_path)
    # АЛЕРТЫ при семантических сбоях (не python-traceback — те видны по rc≠0). brain_down =
    # CRITICAL (модель не ответила ВОВСЕ, инбокс пуст), dropped_stub > 0 = WARN (сеть/парс
    # подводили, но живые идеи БЫЛИ). Может уйти в TG (если ENV) или логируется в stdout.
    if out.get("brain_down"):
        alerts.maybe_alert("CRITICAL", "мозг недоступен — все LLM промолчали, инбокс пуст")
    elif int(out.get("dropped_stub") or 0) > 0:
        alerts.maybe_alert("WARN", f"отсеяно {out['dropped_stub']} stub-болванок (сеть/парс LLM подводили)")


def _rotate_if_needed(path):
    """Обрезать файл runs.md до последних config.MAX_LOG_ENTRIES строк, если вырос сверх лимита.

    Формат runs.md — построчный (1 прогон = 1 строка, парсер serve._read_runs тоже считает
    строками), поэтому «1000 записей» буквально «1000 строк». Ротация: читаем все, если больше
    лимита — оставляем хвост, атомарно переписываем через tmp+os.replace (как _atomic_write в
    harvest_gate, чтобы обрыв записи не бил файл). Нет файла — no-op (первый прогон).
    Идемпотентна: файлы ≤ лимита не трогает."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return  # первый прогон / файл удалён вручную — нечего ротировать
    if len(lines) <= config.MAX_LOG_ENTRIES:
        return
    keep = "".join(lines[-config.MAX_LOG_ENTRIES :])
    # атомарно: во временный рядом + os.replace (обрыв записи НЕ обрежет runs.md)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(keep)
    os.replace(tmp, path)
