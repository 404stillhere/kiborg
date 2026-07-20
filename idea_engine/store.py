# fmt: off
# Замороженное ядро (гейт человека, см. README). Black/ruff НЕ форматируют этот файл —
# стабильность важнее единообразия стиля. Маркер # fmt: off — документированная гарантия black.
"""Ядро первого среза киборга — две дорожки с потолком и обратной тягой.

Дорожка A (ideas): новые идеи. ПОТОЛОК = cap (по умолчанию 3). Обратная тяга:
    добавить новую нельзя, пока дорожка полна открытыми идеями; место
    освобождается, только когда юзер разгребёт (пометит take / later / trash).
Дорожка B (finish): один слот-напоминание «доделать существующее». Обновляется,
когда дорожка A полна, — чтобы киборг не простаивал.

Это чистая логика: ни сети, ни ключей, ни внешних путей внутри решений —
только состояние в переданном файле. Оболочка (run.py) кормит её органами.
"""
import contextlib
import json
import os
import re
import time

DEFAULT_CAP = 3
_SEEN_CAP = 5000       # потолок памяти предложенного: помним последние N заголовков. Поднят 500→5000
                       # (режим «максимум качества»): больше памяти новизны = меньше повторов со временем


@contextlib.contextmanager
def state_lock(path, timeout=5.0, poll=0.03):
    """Best-effort МЕЖПРОЦЕССНЫЙ замок вокруг read-modify-write state.json (файл пишут РАЗНЫЕ
    процессы: пульт-триаж, CLI-harvest, прогон). Порчу файла уже снял atomic save(); тут — про
    ПЕРЕЗАПИСЬ (lost-update). Замок = атомарное O_EXCL-создание lockfile: ОС гарантирует, что fd
    получит ровно ОДИН процесс (отсюда взаимное исключение, оно НЕ требует live-мультипроцесса для
    проверки — примитив юнит-тестируем, гарантия от ОС). Ждём освобождения до timeout; не дождались
    (держат / стейл после краша) → ПРОХОДИМ без лока (дедлока НЕТ, редкий стейл сам разберётся).
    Полной сериализации не обещаем — это безопасное СМЯГЧЕНИЕ окна гонки. Чужой лок при проходе НЕ
    трогаем (снимаем только свой)."""
    lockpath = path + ".lock"
    fd, waited = None, 0.0
    while waited < timeout:
        try:
            fd = os.open(lockpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            time.sleep(poll)
            waited += poll
    try:
        yield fd is not None          # True = держим лок эксклюзивно; False = прошли по timeout
    finally:
        if fd is not None:            # снимаем ТОЛЬКО свой лок (чужой, что держат при проходе, не трогаем)
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.remove(lockpath)
            except OSError:
                pass


# служебные + ультра-общие слова: сами по себе идею НЕ различают, из сравнения на дубль убираем
# (иначе «трекер сна» и «трекер финансов» схлопнутся: общие «бот/для/трекер» дают Jaccard 0.6)
_STOP = {
    "для", "на", "с", "и", "в", "по", "из", "о", "от", "до", "за", "к", "у", "а", "но", "или",
    "же", "ли", "бы", "что", "как", "это", "при", "об", "во", "со", "не", "без", "the", "a", "an",
    "for", "of", "to", "and", "with", "in", "on", "at", "by", "or", "as", "is", "be",
    "бот", "система", "приложение", "платформа", "сервис", "инструмент", "app", "tool", "system", "platform",
}


def _norm(t):
    return re.findall(r"[a-zа-яё0-9]+", (t or "").lower())


def _content(title):
    """Значимые слова заголовка (без служебных/ультра-общих) — по ним сравниваем на дубль."""
    return [t for t in _norm(title) if t not in _STOP]


def _sig(title):
    return " ".join(_content(title))

OPEN = "open"
TAKE = "take"
LATER = "later"
TRASH = "trash"
_CLEARED = {TAKE, LATER, TRASH}
_VALID = _CLEARED | {OPEN}


class Store:
    def __init__(self, path, cap=DEFAULT_CAP):
        self.path = path
        self.data = {
            "cap": cap,
            "tick": 0,
            "seq": 0,
            "cursor": 0,      # ротация проектов в режиме B
            "ideas": [],       # дорожка A
            "finish": None,    # дорожка B (один слот)
            "seen": [],        # память предложенного: сигнатуры заголовков (растёт, не чистится)
        }
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        self.data["cap"] = cap  # cap — конфиг, а не состояние: конструктор авторитетен
        # старое состояние без «seen» — засеять из уже бывших идей, чтобы их не повторять
        if not self.data.get("seen"):
            self.data["seen"] = [_sig(i.get("title", "")) for i in self.data["ideas"]]

    def save(self):
        # Атомарно: пишем во временный файл рядом и подменяем через os.replace — обрыв в момент
        # записи НЕ оставит усечённый state.json (иначе следующий json.load падает, инбокс мёртв).
        # tmp с pid: state.json реально пишут РАЗНЫЕ процессы (живой deliver + триаж-спавн с пульта),
        # уникальное имя снимает гонку за общий .tmp. (Lost-update это НЕ лечит — нужен замок в serve.)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):     # обрыв сериализации — убрать огрызок, оригинал цел
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise

    # --- дорожка A: новые идеи ---
    def open_ideas(self):
        return [i for i in self.data["ideas"] if i["status"] == OPEN]

    def _unlimited(self):
        return self.data.get("cap") in (None, 0)   # 0/None = копилка без потолка

    def has_room(self):
        return self._unlimited() or len(self.open_ideas()) < self.data["cap"]

    def _is_dup(self, idea):
        """Уже предлагали похожее? Сравнение по ЗНАЧИМЫМ словам заголовка.

        Правило (precision > recall — потерять НОВУЮ идею дороже, чем пропустить дубль,
        который юзер просто отправит в мусор):
          • новая ⊆ виденной  → дубль (нового значимого слова не добавляет);
          • новая ⊋ виденной  → НЕ дубль (несёт слово, которого у виденной не было:
            «трекер сна» уже был → «трекер сна и настроения» пропускаем — это про настроение);
          • пересечение без вложения → близость по Jaccard>=0.6 (обе стороны с уникальными
            словами — в основном одно и то же).
        Раньше был чистый Jaccard>=0.6: он схлопывал ПОДмножества («трекер сна» vs
        «трекер сна и настроения» = 2/3=0.67) и молча ел более богатую идею (аудит 2026-07-14)."""
        toks = set(_content(idea.get("title", "")))
        if not toks:
            return False
        for s in self.data.get("seen", []):
            st = set(s.split())
            if not st:
                continue
            if toks <= st:            # новая не добавляет ничего сверх виденной → дубль (в т.ч. равные)
                return True
            if toks >= st:            # новая несёт лишнее значимое слово → это НЕ дубль (смотрим дальше)
                continue
            union = len(toks | st)
            if union and len(toks & st) / union >= 0.6:
                return True
        return False

    def add_idea(self, idea):
        """Добавляет идею, если есть место (обратная тяга) И её ещё не предлагали. True/False."""
        if not self.has_room():
            return False
        if self._is_dup(idea):
            return False              # память предложенного: похожее уже было — не повторяем
        sig = _sig(idea.get("title", ""))
        if sig:                       # пустую сигнатуру (заголовок из одних служебных слов) не копим
            self.data["seen"].append(sig)
            if len(self.data["seen"]) > _SEEN_CAP:
                self.data["seen"] = self.data["seen"][-_SEEN_CAP:]  # помним последние N
        self.data["seq"] += 1
        # служебные поля идут ПОСЛЕ idea — она не может подделать id/status/born_tick
        rec = {**idea, "id": self.data["seq"], "status": OPEN, "born_tick": self.data["tick"]}
        self.data["ideas"].append(rec)
        return True

    def set_status(self, idea_id, status):
        if status not in _VALID:
            raise ValueError(f"bad status: {status}")
        target = None
        for i in self.data["ideas"]:
            if i["id"] == idea_id:
                target = i
                break
        if target is None:
            return False
        # переоткрытие (OPEN) обязано уважать потолок — вторая дверь в open не пробивает cap
        if status == OPEN and target["status"] != OPEN and not self.has_room():
            return False
        target["status"] = status
        return True

    def cleared_count(self):
        return len([i for i in self.data["ideas"] if i["status"] in _CLEARED])

    # --- дорожка B: доделать существующее ---
    def set_finish(self, nudge, cursor):
        self.data["finish"] = nudge
        self.data["cursor"] = cursor
