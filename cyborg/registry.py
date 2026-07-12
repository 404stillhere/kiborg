"""Каталог органов — «книга инструментов» из _shared/organs.json (89 карточек).

Информационный слой: показать киборгу, какие органы вообще существуют. Исполняемые
органы подключаются отдельно (core.Organ через wiring) — большинство карточек требуют
своих runtime/ключей/сети, и в бете сами не запускаются. Растим исполняемый набор
по одному (как советовал совет 5 моделей: не подключать все 47 разом).
"""
import json

DEFAULT_CATALOG = "M:/projects/_shared/organs.json"


class OrganCard:
    def __init__(self, d):
        self.name = d.get("name")
        self.project = d.get("project")
        self.purpose = d.get("purpose", "")
        self.needs_keys = d.get("needs_keys") or []
        self.status = d.get("status")
        self.runtime = d.get("runtime")
        self.call = d.get("call")
        self.raw = d


def load_catalog(path=DEFAULT_CATALOG):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    organs = d.get("organs", d if isinstance(d, list) else [])
    return [OrganCard(o) for o in organs]
