"""Опциональные уведомления о доставленных идеях в Telegram.

Читает KIBORG_NOTIFY_TOKEN и KIBORG_NOTIFY_CHAT_ID из окружения. Если заданы — шлёт
краткую сводку в чат при доставке идей. Не роняет прогон при сетевых сбоях.
Только stdlib (urllib).
"""

import os
import urllib.parse
import urllib.request

import config


def _send(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    urllib.request.urlopen(req, timeout=config.ALERT_HTTP_TIMEOUT).read()


def notify_delivered(count, titles):
    """Уведомить о доставленных идеях. count — число, titles — список строк."""
    token = os.environ.get("KIBORG_NOTIFY_TOKEN")
    chat_id = os.environ.get("KIBORG_NOTIFY_CHAT_ID")
    if not token or not chat_id:
        return
    if count <= 0:
        return
    lines = [f"[kiborg] Доставлено идей: {count}"]
    for t in titles[:10]:
        lines.append(f"- {t}")
    if len(titles) > 10:
        lines.append(f"... и ещё {len(titles) - 10}")
    text = "\n".join(lines)
    try:
        _send(token, chat_id, text)
    except Exception:
        pass
