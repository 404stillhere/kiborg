"""Лёгкий in-memory счётчик таймаутов state_lock для /api/health.

Зачем: после внедрения stale-lock-cleanup (cyborg/wiring_collect._remove_stale_lock)
зависшие lock-файлы сносятся автоматически, и таймауты state_lock стали редкими.
Но когда живой конкурент ДЕЙСТВИТЕЛЬНО держит лок > TG_LOCK_TIMEOUT (130с) — это
сигнал, что что-то не так (зависший прогон, pyrogram-зомби). Администратор должен
видеть это на пульте, не грепая логи.

Хранение: список Unix-timestamps в ОП (НЕ на диск — это ephemeral метрика процесса,
не persisted state; per-process). Каждый процесс panel/serve.py держит свой счётчик
(отражает таймауты прогона ЭТОГО процесса; другие harvest-процессы сюда не пишут —
это сознательное упрощение: пульт = один процесс).

Потокобезопасность: threading.Lock (НЕ state_lock — тот межпроцессный file-lock,
этот внутренний, в пределах процесса). Таски ThreadingHTTPServer идут из разных
потоков → счётчик под гонкой без лока.

Очистка устаревших записей: делается lazy при вызове recent_timecuts() — заодно с
подсчётом, чтобы список не рос бесконечно при долговечной службе.
"""

import threading
import time

# Список временных меток таймаутов (Unix epoch, float). Append-only кроме cleanup.
# Под _LOCK — публичных мутаций напрямую нет, только через record/recent API.
_TIMECTS: list = []
_LOCK = threading.Lock()


def record_timeout():
    """Зафиксировать таймаут state_lock (вызывает _collect_locked при warn).

    Просто добавляет текущий timestamp в список под локом. Дешёвая операция,
    не блокирует прогон. Cleanup устаревших записей происходит позже, в recent_timeouts.
    """
    now = time.time()
    with _LOCK:
        _TIMECTS.append(now)


def recent_timeouts(minutes=60):
    """Сколько таймаутов произошло за последние `minutes` минут.

    Заодно (под тем же локом) сносим из списка всё, что старше окна — список не
    растёт бесконечно при долгой жизни процесса. Возвращает int-количество.
    """
    cutoff = time.time() - minutes * 60
    with _LOCK:
        # in-place filter: оставляем только свежие, устаревшие выкидываем навсегда.
        kept = [t for t in _TIMECTS if t >= cutoff]
        _TIMECTS[:] = kept
        return len(kept)


def reset():
    """Очистить счётчик (только для тестов — изоляция между тест-кейсами)."""
    with _LOCK:
        _TIMECTS[:] = []
