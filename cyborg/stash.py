"""Копилка идей для АВТОНОМНЫХ прогонов киборга — растёт БЕЗ потолка, с дедупом.

Зачем: инбокс idea_engine нарочно держит потолок 3 (обратная тяга — не заваливать юзера).
Но когда Claude гоняет киборга сам, пока юзер спит/ушёл, нужен ровно обратный режим:
пусть идеи КОПЯТСЯ горой, а разберёт человек, когда вернётся. Это и есть копилка.

Дедуп переиспользует нормализацию заголовков из idea_engine/store.py (единый критерий
похожести — «трекер сна» и «трекер финансов» не схлопываются, а точный повтор не пишется дважды).
Два файла: .jsonl (машинный лог, одна идея на строку) + .md (человекочитаемый список, свежее сверху).
"""
import datetime
import json
import os
import sys

_IDEA = "M:/projects/kiborg/idea_engine"
if _IDEA not in sys.path:
    sys.path.insert(0, _IDEA)
import store as _ie_store  # noqa: E402  переиспользуем _sig/_content/Jaccard-логику дедупа

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
JSONL_PATH = os.path.join(DATA, "idea_stash.jsonl")
MD_PATH = os.path.join(DATA, "idea_stash.md")
_DUP_THRESHOLD = 0.6  # тот же порог Jaccard, что в store (единый критерий «уже предлагали»)


class Stash:
    """Копилка на диске. Загружается из .jsonl, add() дедупит и копит, save() пишет оба файла."""

    def __init__(self, jsonl=None, md=None):
        # резолвим из модульных глобалов ПРИ ВЫЗОВЕ (а не в дефолте сигнатуры) —
        # иначе monkeypatch путей в тестах не сработал бы, и sink писал бы в реальную копилку
        self.jsonl = jsonl or JSONL_PATH
        self.md = md or MD_PATH
        self.ideas = []
        self._seen = []  # сигнатуры заголовков (для дедупа), параллельно self.ideas и старым
        if os.path.exists(self.jsonl):
            with open(self.jsonl, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    self.ideas.append(d)
                    self._seen.append(_ie_store._sig(d.get("title", "")))

    def _is_dup(self, idea):
        """Уже копили похожее? Сравнение по значимым словам заголовка (как в инбоксе)."""
        toks = set(_ie_store._content(idea.get("title", "")))
        if not toks:
            return False
        for s in self._seen:
            st = set(s.split())
            if not st:
                continue
            if st == toks:
                return True
            union = len(toks | st)
            if union and len(toks & st) / union >= _DUP_THRESHOLD:
                return True
        return False

    def add(self, idea, now=None):
        """Добавляет идею, если её ещё не копили. True/False. Штампует время добавления."""
        if not isinstance(idea, dict) or self._is_dup(idea):
            return False
        rec = dict(idea)
        rec.setdefault("source", "cyborg")
        rec["stashed_at"] = (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M")
        self.ideas.append(rec)
        sig = _ie_store._sig(idea.get("title", ""))
        if sig:  # пустую сигнатуру не копим (как store) — иначе засоряет _seen без пользы
            self._seen.append(sig)
        return True

    @staticmethod
    def _atomic_write(path, text):
        """Пишем во временный файл рядом и атомарно подменяем (os.replace) — обрыв в момент
        записи НЕ обрежет копилку: старый файл цел до последнего шага. Защита от потери всего стэша."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)  # атомарно на той же ФС

    def save(self):
        jsonl = "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in self.ideas)
        self._atomic_write(self.jsonl, jsonl)
        self._atomic_write(self.md, self._md_text())

    def _md_text(self):
        lines = [f"# Копилка идей киборга — {len(self.ideas)} шт.",
                 "",
                 "_Идеи, собранные в автономных прогонах. Свежие сверху. Разбирай, когда есть время._",
                 ""]
        for d in reversed(self.ideas):  # свежее сверху
            brain = "★ ИИ" if d.get("brain") == "llm" else "болванка"
            when = d.get("stashed_at", "")
            lines.append(f"## {d.get('title', '(без названия)')}")
            lines.append(f"{d.get('why', '')}")
            lines.append(f"— сложность: {d.get('effort', '?')} · {brain} · собрано {when}")
            lines.append("")
        return "\n".join(lines)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    s = Stash()
    before = len(s.ideas)
    s.add({"title": "Смоук-идея копилки", "why": "проверка", "effort": "легко", "brain": "stub"})
    s.save()
    print(f"копилка: было {before}, стало {len(s.ideas)} -> {s.md}")
