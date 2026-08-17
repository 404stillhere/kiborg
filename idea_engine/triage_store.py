"""Разобранные идеи — «взятые» (taken) и «отложенные» (later). Два отдельных master-файла
рядом с state.json и rejected.json.

    data/taken.json  — {"taken":  [{...полная идея..., "triaged_ts": "YYYY-MM-DD HH:MM:SS"}, ...]}
    data/later.json  — {"later":  [{...полная идея..., "triaged_ts": "YYYY-MM-DD HH:MM:SS"}, ...]}

Пишется при триаже (idea_engine/run.py: take/later — идея уходит из state.json и ложится
сюда целиком, с меткой времени действия). Читается пультом (panel/serve.py: _read_inbox
отдаёт taken/later в /api/state → UI рисует «Разобранные»).

Отличие от rejected.py: храним ПОЛНУЮ идею (id/title/why/score/born_tick/…), без дедупа и
без потолка — взятые/отложенные идеи не должны теряться. Только stdlib (пульт импортит без
venv). Атомарность — tmp-файл + os.replace (как в rejected.py и store.py)."""

import datetime
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cyborg"))

import config

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TAKEN_PATH = os.path.join(DATA, config.TAKEN_FILE)
LATER_PATH = os.path.join(DATA, config.LATER_FILE)


class CorruptedError(Exception):
    """Файл разобранного битый (не-JSON / не словарь). Читать показываем пусто,
    ПЕРЕЗАПИСЫВАТЬ запрещено: тихая потеря всей истории при ручной правке с опечаткой
    (council 2026-08-17, находка #3)."""


def quarantine_corrupted(path, why):
    """Карантин битого файла: копия <path>.corrupted-<TS> + CRITICAL-алерт.

    Один раз на инцидент: если копия новее самого файла — уже копировали, не спамим.
    Общий хелпер для taken/later (тут) и rejected (импортит его)."""
    try:
        olds = glob.glob(f"{path}.corrupted-*")
        if olds and max(os.stat(o).st_mtime for o in olds) >= os.stat(path).st_mtime:
            return
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        qpath = f"{path}.corrupted-{ts}"
        shutil.copy2(path, qpath)
    except OSError:
        qpath = None  # копия не вышла — всё равно отказываем записи, файл не трогаем
    msg = (
        f"[triage] ФАЙЛ БИТЫЙ: {os.path.basename(path)} ({why}). "
        + (f"Копия сохранена: {qpath}. " if qpath else "")
        + "Запись заблокирована до починки руками; действие отменено."
    )
    print(msg)
    try:
        import alerts  # noqa: E402  (cyborg в sys.path; пульт без venv — stdlib-only)

        alerts.maybe_alert("CRITICAL", msg)
    except Exception:
        pass


def _load(path):
    """Прочитать {<key>: [...]} с диска. Нет файла → [] (первый запуск — норма).
    Битый (не-JSON / не-словарь) → карантин-копия + CorruptedError: показ пустой,
    но add() ОТКАЖЕТ — перезаписать битый файл одним новым элементом нельзя.

    encoding='utf-8-sig': BOM от блокнотной правки — валидный файл, читается без
    аварии (utf-8 бросил бы JSONDecodeError на первый же байт)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except FileNotFoundError:
        return []
    except ValueError as exc:  # JSONDecodeError/UnicodeDecodeError — его подклассы
        quarantine_corrupted(path, str(exc)[:120])
        raise CorruptedError(os.path.basename(path)) from None
    if not isinstance(d, dict):
        quarantine_corrupted(path, "JSON не словарь")
        raise CorruptedError(os.path.basename(path)) from None
    # принимаем оба ключа (taken/later) — caller передаёт путь, формат детерминирован
    for k in config.TRIAGE_STORE_KEYS:
        v = d.get(k)
        if isinstance(v, list):
            return v
    return []


def _save(path, items, key):
    """Атомарно записать {key: items}: tmp-файл + os.replace (обрыв записи не бьёт файл).
    tmp с pid: файл могут писать разные процессы (триаж-спавн пульта), уникальное имя
    снимает гонку за общий .tmp (как в rejected.py / store.py)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{config.ATOMIC_TMP_PID_SUFFIX.format(pid=os.getpid())}"
    try:
        with open(tmp, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
            json.dump({key: items}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def _key_for(path):
    """Имя ключа ('taken' / 'later') по пути файла — для atomic_save."""
    base = os.path.basename(path)
    return (
        config.TRIAGE_STORE_TAKEN_KEY
        if base.startswith(config.TRIAGE_STORE_TAKEN_PREFIX)
        else config.TRIAGE_STORE_LATER_KEY
    )


def add(path, idea):
    """Добавить ПОЛНУЮ идею в файл (taken.json или later.json) с меткой времени действия.
    Идемпотентно по id: если идея с тем id уже в файле — не дублируем (защита от повторного
    триажа одной идеи, напр. при гонке двух процессов). Возвращает обновлённый список.
    Битый файл → CorruptedError: вызывающий триаж отменяется (идея остаётся в инбоксе),
    история не перезаписывается."""
    key = _key_for(path)
    items = _load(path)  # битый → исключение вверх, БЕЗ перезаписи
    iid = idea.get("id")
    if iid is not None and any(it.get("id") == iid for it in items):
        return items  # уже разобрана — не дублируем
    idea = dict(idea)  # не мутируем вызывающий словарь
    idea.setdefault("triaged_ts", datetime.datetime.now().strftime(config.DATETIME_FMT))
    items.append(idea)
    _save(path, items, key)
    return items


def load(path):
    """Полный список разобранных идей из файла — для пульта ({"taken": [...]}/{...}).
    Возвращает {<key>: [...]}, пустой каркас при отсутствии; битый → тоже пустой
    (запись при этом заблокирована add()-ом, карантин-копия уже снята в _load)."""
    key = _key_for(path)
    try:
        items = _load(path)
    except CorruptedError:
        items = []
    return {key: items}


def count(path):
    """Сколько идей в файле (битый → 0, показ не роняем)."""
    try:
        return len(_load(path))
    except CorruptedError:
        return 0


if __name__ == "__main__":
    print("taken:", count(TAKEN_PATH), "· later:", count(LATER_PATH))
