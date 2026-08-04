"""z.ai Anthropic-совместимый транспорт для интуиции.

Нужен потому что DarBench/organ.js говорит только OpenAI /chat/completions,
а z.ai предоставляет /api/anthropic/v1/messages. Этот модуль — тонкая обёртка
над urllib (только stdlib), повторяющая контракт ask_llm.ask: prompt -> text | "".

Ключ берётся из env ZAI_API_KEY или из llm_keys.env (через keychain.load_keys).
Без ключа / при сбое возвращает "", чтобы ask_llm честно упал на fallback-цепочку.
"""

import json
import os
import urllib.request

import config
import keychain

_ZAI_URL = os.environ.get("KIBORG_ZAI_URL", "https://api.z.ai/api/anthropic/v1/messages")
_MODEL = os.environ.get("KIBORG_ZAI_MODEL", "glm-5.2")
_TIMEOUT = int(os.environ.get("KIBORG_ZAI_TIMEOUT_MS", "120000"))


def _api_key():
    """Вернуть ZAI_API_KEY из env или llm_keys.env."""
    env = os.environ.get("ZAI_API_KEY", "")
    if env:
        return env
    return keychain.load_keys().get("ZAI_API_KEY", "")


def available():
    """Есть ли ключ z.ai."""
    return bool(_api_key())


def ask(prompt, timeout_ms=None, max_tokens=8192, temperature=0.9):
    """Один вызов z.ai Anthropic /v1/messages. -> text | ""."""
    key = _api_key()
    if not key:
        return ""
    timeout_ms = timeout_ms or _TIMEOUT
    url = _ZAI_URL
    body = json.dumps(
        {
            "model": _MODEL,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            config.HTTP_HEADER_CONTENT_TYPE: config.HTTP_MEDIA_TYPE_JSON,
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_ms / 1000) as r:
            raw = r.read().decode("utf-8", "replace")
            data = json.loads(raw)
            content = data.get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text") or ""
            return ""
    except Exception:
        return ""


if __name__ == "__main__":
    if not available():
        print("SMOKE SKIP: ZAI_API_KEY не задан")
    else:
        out = ask('Верни РОВНО одну строку JSON и ничего больше: {"ok":true}')
        print("SMOKE", "OK" if '"ok"' in out or "ok" in out.lower() else "FAIL", "|", repr(out[:160]))
