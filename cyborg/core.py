"""Базовые типы киборга: исполняемый Орган и Память (env.memory)."""


class Organ:
    """Обёртка над органом-функцией run(inputs, env) + метаданные для роутера/исполнителя.

    role     — 'source' (создаёт данные), 'transform' (преобразует), 'sink' (выдаёт наружу).
    produces — какие ключи памяти орган кладёт (что можно ждать на выходе).
    consumes — какие ключи памяти органу нужны на входе.
    tags     — слова для роутера (в т.ч. по-русски, чтобы матчить цель).
    needs    — {'network':bool, 'key':<имя>, 'prod':bool, 'stub_ok':bool} для прод-гейта.
    """

    def __init__(self, name, purpose, run, role="transform",
                 produces=None, consumes=None, tags=None, needs=None):
        self.name = name
        self.purpose = purpose
        self.run = run
        self.role = role
        self.produces = produces or []
        self.consumes = consumes or []
        self.tags = tags or []
        self.needs = needs or {}


class Memory:
    """env.memory — накопительный контекст, протаскиваемый сквозь все вызовы органов.

    data    — сами данные по ключам (items, ideas, nudge, ...).
    blocked — органы, что упали или были прод-гейтнуты: их больше не переизбираем
              (это и есть перепланирование — пробуем другой путь, не долбим один).
    trace   — журнал наблюдений.
    """

    def __init__(self):
        self.data = {}
        self.produced = set()   # ключи, ЗАПИСАННЫЕ хоть раз (даже пустым значением)
        self.blocked = set()
        self.trace = []

    def observe(self, organ_name, result):
        note = {"organ": organ_name, "keys": []}
        if isinstance(result, dict):
            if result.get("error"):
                note["error"] = result["error"]
                self.blocked.add(organ_name)
            if result.get("skipped"):
                note["skipped"] = result["skipped"]
                self.blocked.add(organ_name)
            for k, v in result.items():
                if k in ("error", "skipped"):
                    continue
                self.data[k] = v
                self.produced.add(k)   # орган отработал этот ключ, даже если значение пустое
                note["keys"].append(k)
        self.trace.append(note)
        return note

    def has(self, key):
        v = self.data.get(key)
        return v is not None and v != [] and v != "" and v != {}
