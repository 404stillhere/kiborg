"""Опциональный алертинг при семантических сбоях прогона.

Цель: дать юзеру знать, когда киборг реально встал (не «доставлено 3», а «мозг недоступен,
инбокс пуст» или «сеть/парс LLM подводят, отсеяно N болванок»). Встраивается в harvest_log._log
(там `out` уже на руках после прогона) — НЕ на горячем пути конвейера, а в эпилоге.

Два режима работы maybe_alert(level, message):
  - Токен в окружении (config.ALERT_TOKEN_ENV + ALERT_CHAT_ENV заданы): POST на
    https://api.telegram.org/bot<TOKEN>/sendMessage через urllib.request (stdlib, БЕЗ новой
    зависимости — требование requirements.txt «runtime = чистый stdlib»). Chat ID = из ENV,
    текст = «[kiborg][{level}] {message}». Таймаут ALERT_HTTP_TIMEOUT; любая сетевая ошибка →
    тихо падаем на print (алертинг НЕ должен ронять прогон).
  - Токена нет: print(f"[ALERT][{level}] {message}") — юзер увидит в логе/консоли пульта.

Новые зависимости: НЕТ. urllib.request — stdlib. JSON-тело запроса строим через urllib.parse.urlencode
(form-encoded, как ждёт TG sendMessage с application/x-www-form-urlencoded).

Безопасность: токен бота читается из окружения, не из файла (см. config.ALERT_TOKEN_ENV).
В message НЕТ секретов — туда попадают только счётчики/категории из out (redacted, dropped_stub).
"""

import json  # noqa: F401  (зарезервирован для будущего enriched-payload; сейчас form-encoded)
import os
import urllib.parse
import urllib.request

import config


def _tg_send(token, chat_id, text):
    """POST на api.telegram.org/bot<TOKEN>/sendMessage. Любая ошибка → raise (звавший поймает)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    # urlopen сам по себе выбросит URLError/HTTPError при таймауте/сбое — звавший обработает.
    urllib.request.urlopen(req, timeout=config.ALERT_HTTP_TIMEOUT).read()


def maybe_alert(level, message):
    """Послать алерт уровня `level` (CRITICAL/WARN/...) с текстом `message`.

    Если в окружении есть KIBORG_ALERT_TOKEN и KIBORG_ALERT_CHAT_ID — уходит в Telegram
    (Bot API через urllib, без зависимости). Нет — пишется в stdout с префиксом [ALERT].
    Любой сбой отправки (нет сети, неверный токен, таймаут) — тихо логируется в stdout,
    исключение НЕ прокидывается: алертинг не должен ронять рабочий прогон киборга.
    """
    token = os.environ.get("KIBORG_ALERT_TOKEN")
    chat_id = os.environ.get("KIBORG_ALERT_CHAT_ID")
    line = f"[kiborg][{level}] {message}"
    if not token or not chat_id:
        # Нет конфигурации — логируем. Прогон продолжается, юзер хотя бы увидит в консоли/журнале.
        print(f"[ALERT][{level}] {message}")
        return
    try:
        _tg_send(token, chat_id, line)
    except Exception as e:
        # Сетевой сбой / неверный токен / TG недоступен — НЕ роняем прогон, деградируем на print.
        # Причина в лог — иначе алерт пропадёт тихо и юзер не поймёт, почему TG молчит.
        print(f"[ALERT][{level}] {message} (TG-отправка не удалась: {type(e).__name__}: {e})")
