"""Сборка публичного реестра исполняемых органов (build_organs).

Вынесено из монолита wiring.py — финальная точка сборки: импортирует _run_*-обёртки из
остальных подмодулей wiring и собирает список Organ. Organ (класс карточки) — из core,
читаем через фасад (он там как атрибут `wiring.Organ`).
"""

from wiring_collect import _run_collect
from wiring_council import _run_rank, _run_readability
from wiring_finish import _run_finish
from wiring_ideate import _run_ideate
from wiring_scrub import _run_deliver, _run_finish_sink, _run_scrub


def build_organs():
    import wiring

    return [
        wiring.Organ(
            name="collect_source",
            purpose="Тянет свежие внешние items (новости/сигналы) — сырьё для идей.",
            run=_run_collect,
            role="source",
            produces=["items"],
            consumes=[],
            tags=["собрать", "новости", "свежие", "источник", "идеи", "сигналы", "сырьё"],
            needs={"network": True},
        ),
        wiring.Organ(
            name="ideate",
            purpose="Из items делает МНОГО идей-кандидатов с ценником (судья отберёт лучшие).",
            run=_run_ideate,
            role="transform",
            produces=["ideas"],
            consumes=["items"],
            tags=["идея", "идеи", "идей", "придумать", "предложить"],
            needs={"key": "LLM_KEY", "stub_ok": True},
        ),
        wiring.Organ(
            name="rank_ideas",
            purpose="Судья/совет: из пула идей оставляет топ-5 по рубрике (оригинальность/польза/выполнимость).",
            run=_run_rank,
            role="transform",
            produces=["ideas_best"],
            consumes=["ideas"],
            tags=["идея", "идеи", "отобрать", "лучшие", "оценить", "судья", "ранжировать"],
            needs={"key": "LLM_KEY", "stub_ok": True},
        ),
        wiring.Organ(
            name="finish_step",
            purpose="Режим 'доделать': достаёт следующий шаг по существующим проектам.",
            run=_run_finish,
            role="source",
            produces=["nudge"],
            consumes=[],
            tags=["доделать", "существующие", "проекты", "шаг", "финиш", "довести"],
            needs={},
        ),
        wiring.Organ(
            name="readability_gate",
            purpose="Редактор читаемости: карточку с мутным описанием (балл<7) переписывает самонесущей, идею не теряя.",
            run=_run_readability,
            role="transform",
            produces=["ideas_polished"],
            consumes=["ideas_best"],
            tags=["читаемость", "понятно", "описание", "идеи", "редактор", "ясно"],
            needs={"key": "LLM_KEY", "stub_ok": True},
        ),
        wiring.Organ(
            name="scrub_secrets",
            purpose="Защитный проход: вычищает креды (sk-/ghp-/AIza/KEY=…) из текста идей перед доставкой.",
            run=_run_scrub,
            role="transform",
            produces=["ideas_safe"],
            consumes=["ideas_polished"],
            tags=["безопасно", "секрет", "очистить", "идеи", "защита"],
            needs={},
        ),
        wiring.Organ(
            name="deliver",
            purpose="Доставляет идеи в инбокс (cap=0 — без потолка, inbox.md; при живом ключе фильтрует stub-болванки).",
            run=_run_deliver,
            role="sink",
            produces=["delivered"],
            consumes=["ideas_safe"],
            tags=["доставить", "идеи", "инбокс", "прислать", "приноси", "свежие"],
            needs={},
        ),
        wiring.Organ(
            name="finish_sink",
            purpose="Доводит подсказку «доделай» до инбокса (через deliver), вычистив секреты из recon.",
            run=_run_finish_sink,
            role="sink",
            produces=["delivered"],
            consumes=["nudge"],
            tags=["доделать", "довести", "шаг", "инбокс", "проекты"],
            needs={},
        ),
    ]
