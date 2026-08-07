"""Клиент для управления mc-bot через его локальный control API.

Пульт kiborg (panel) использует этот модуль для проксирования команд в mc-bot:
GET  /api/mcbot/status  → состояние бота (heartbeat из лога)
POST /api/mcbot/cmd     → отправить команду (стоп, копай, статус, ...)

Конфигурация — через env:
  MCBOT_CONTROL_HOST  (default 127.0.0.1)
  MCBOT_CONTROL_PORT  (default 7654)
  MCBOT_CONTROL_TOKEN (обязательный, без него клиент disabled)

Безопасность: token не логируется, запросы только на localhost.
"""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Метаданные — сюда control API mc-bot пишет heartbeat и события.
# Пульт не ходит в mc-bot за логом постоянно, а читает последний heartbeat.
MCBOT_LOG_FILE = os.environ.get("MCBOT_LOG_FILE", "M:/projects/mc-bot/logs/live-verify-20260807-stdout.log")


class McBotClient:
    """Узкий клиент: только POST /control."""

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None, token: Optional[str] = None):
        self.host = host or os.environ.get("MCBOT_CONTROL_HOST", "127.0.0.1")
        self.port = int(port or os.environ.get("MCBOT_CONTROL_PORT", "7654"))
        self.token = token or os.environ.get("MCBOT_CONTROL_TOKEN", "")
        self.enabled = bool(self.token)
        # Windows: системный прокси может перехватывать loopback и давать 502.
        # В тестах панели используется тот же приём.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _url(self, path: str = "/control") -> str:
        return f"http://{self.host}:{self.port}{path}"

    def send_command(self, command: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Отправить команду в mc-bot. Возвращает {"ok": bool, ...}."""
        if not self.enabled:
            return {"ok": False, "error": "MCBOT_CONTROL_TOKEN not configured"}
        if not command or not isinstance(command, str):
            return {"ok": False, "error": "empty command"}
        payload = json.dumps({"token": self.token, "command": command.strip()}).encode("utf-8")
        req = urllib.request.Request(
            self._url("/control"),
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
            method="POST",
        )
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")[:500]
            return {"ok": False, "error": f"http {e.code}: {body}"}
        except urllib.error.URLError as e:
            return {"ok": False, "error": f"connect failed: {e.reason}"}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"bad json: {e}"}
        except Exception as e:  # noqa: BLE001 — защита от неожиданного
            return {"ok": False, "error": f"unexpected: {e}"}

    def read_status(self, log_path: Optional[str] = None) -> Dict[str, Any]:
        """Прочитать последнее состояние из лога mc-bot.

        Пульт не имеет постоянного соединения с ботом, поэтому heartbeat-лог —
        единственный источник live-статуса. Если лог недоступен, возвращает
        connected=False.
        """
        path = log_path or MCBOT_LOG_FILE
        if not os.path.exists(path):
            return {"ok": False, "connected": False, "error": "log file not found"}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "connected": False, "error": f"read failed: {e}"}

        # Ищем последнюю строку heartbeat. Логгер mc-bot пишет:
        #   [time] [pid] [INFO] [Universal] heartbeat {"pos":...}
        # (heartbeat — message/scope, а не JSON-поле, поэтому ищем по слову).
        status_line = None
        for line in reversed(lines):
            if "heartbeat" in line:
                status_line = line
                break
        if not status_line:
            return {"ok": True, "connected": False, "error": "no heartbeat in log"}

        parsed = _parse_heartbeat(status_line)
        parsed["ok"] = True
        parsed["connected"] = bool(parsed.get("pos"))
        return parsed


def _parse_heartbeat(line: str) -> Dict[str, Any]:
    """Извлечь JSON из строки лога mc-bot. Логгер пишет префикс [time] [pid] [level] [scope]."""
    # Найти начало JSON: первый '[' после scope или первый '{'.
    # Пример: [2026-08-07T10:55:26.008Z] [25068] [INFO] [Universal] heartbeat {"pos":...}
    json_start = line.find("{ ")
    if json_start == -1:
        json_start = line.find("{")
    if json_start == -1:
        return {"raw": line.strip()[:200]}
    try:
        data = json.loads(line[json_start:])
    except json.JSONDecodeError:
        return {"raw": line.strip()[:200]}
    return {
        "pos": data.get("pos"),
        "dimension": data.get("dimension"),
        "health": data.get("health"),
        "food": data.get("food"),
        "state": data.get("state"),
        "timestamp": _extract_timestamp(line),
    }


def _extract_timestamp(line: str) -> Optional[str]:
    m = re.search(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\]", line)
    return m.group(1) if m else None


def default_client() -> McBotClient:
    return McBotClient()
