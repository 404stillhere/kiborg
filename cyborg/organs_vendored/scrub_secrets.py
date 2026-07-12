"""Орган: scrub_secrets — вычищает очевидные креды из текста перед выходом наружу
(в лог / инбокс / отправку). Чистый: только stdlib `re`, без ключей/сети/прода.

ВЕНДОРЕН (копия) из реестра: organ `scrub_secrets` [Claude Code API Dual Mode]
(`M:/projects/_shared/organs.json`). Оригинал НЕ трогаем — здесь автономная копия
внутри kiborg, чтобы не импортировать чужое прод-дерево (сохраняет прод-изоляцию cyborg).

Контракт: run(inputs, env) -> {"text": <очищенный>, "redacted": <bool>}.
"""
from __future__ import annotations

import re

NEEDS_KEYS: list = []

_SECRET_PATTERNS = [
    re.compile(r"(?i)(sk|xai|ghp|gho|ghs|github_pat|AIza)[-_][A-Za-z0-9_\-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?im)^(\s*(?:[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)\s*[=:]\s*)\S+"),
    # сырой Telegram bot-token <id>:<auth> — ровно тот класс креда, что лежит
    # открытым текстом в recon.next_step по проектам («ротация токена бота 12345:AAH…»);
    # не покрывался прежними паттернами (нет sk-/KEY=/bearer). 30+ символов auth-части
    # и 6–12 цифр id отсекают ложняки (порт :8080, время 12:34).
    re.compile(r"\d{6,12}:[A-Za-z0-9_\-]{30,}"),
    # Google-токен формата AQ.<base64url> (так выглядит ключ Gemini) — прежние паттерны
    # его не ловили (не AIza-/sk-/KEY=). Префикс AQ. + 20+ токен-символов отсекает ложняки.
    re.compile(r"AQ\.[A-Za-z0-9_\-]{20,}"),
]


def scrub_text(text):
    """Заредактить похожие на креды подстроки. Переиспользуемый helper."""
    if not text:
        return text
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) + "[REDACTED]") if m.groups() else "[REDACTED]", text)
    return text


def run(inputs, env):
    text = str((inputs or {}).get("text") or "")
    scrubbed = scrub_text(text)
    return {"text": scrubbed, "redacted": scrubbed != text}


if __name__ == "__main__":
    demo = "config:\nAPI_KEY=sk-abc1234567890abcdef1234\nplain line stays\n"
    out = run({"text": demo}, {})
    print(out)
    print("SMOKE", "OK" if out["redacted"] and "sk-abc" not in out["text"] else "FAIL")
